from __future__ import annotations

import json
import logging

import psycopg

from payload import ScraperPayload
from pipeline import ClassificationResult

logger = logging.getLogger("classifier")

# HISTÓRICO (2026-08-25 a 2026-08-28): este módulo já teve DUAS famílias de
# função lado a lado -- a família antiga (`classification_runs` +
# `classifications` + `analyser_notifications`, schema próprio do
# Classifier) e `persist_classification_result_v2` (schema novo,
# `tabela_classificacao` no System DB compartilhado `app_db2`).
#
# A família antiga foi REMOVIDA (não só desconectada) nesta revisão, porque
# `src/message_handler.py` foi religado para `persist_classification_result_v2`
# e as tabelas antigas não existem mais em `app_db2` -- mantê-las chamáveis
# só convidava alguém a invocá-las contra o banco de produção errado e
# tomar um erro confuso. As migrations old (`db/migrations/0002`-`0004`) NÃO
# foram removidas -- ainda aplicam em `classifier_test` e não há necessidade
# comprovada de apagá-las agora (ver classifier-prisma/context.md, seção
# 19.2 sobre não fazer mudança destrutiva sem necessidade).
#
# Duas lacunas que bloqueavam esse rewire e como foram resolvidas aqui:
#   1. `id_midia` -- resolvida com `resolve_id_midia` (lookup por nome em
#      `tabela_midia`, ver docstring abaixo). NÃO é um mapeamento garantido
#      -- ver pendência documentada lá.
#   2. Notificação ao Analyser -- decisão da Bea (2026-08-28): publicar via
#      RabbitMQ no mesmo padrão do Scraper->Classifier (ver
#      `src/analyser_notifier.py`), não mais o outbox transacional em
#      `analyser_notifications` (tabela que não existe em `app_db2`). Isso
#      supera o que a ADR 0002 tinha decidido antes -- ver ADR 0003.


def resolve_id_midia(conn: psycopg.Connection, source_name: str | None) -> int | None:
    """Resolve o FK `id_midia` (`tabela_midia`, System DB `app_db2`) a partir
    de `ScraperPayload.source_name` (texto livre, ex.: "G1", "Folha").

    Estratégia: lookup EXATO case-insensitive contra `tabela_midia.nome_midia`
    (`LOWER(nome_midia) = LOWER(source_name)`) -- não faz fuzzy/substring,
    para não arriscar associar a notícia a um veículo errado silenciosamente.
    `tabela_midia` tem hoje 10 linhas com nomes completos confirmados direto
    no banco (ex.: "Folha de S.Paulo", "O Estado de S. Paulo (Estadão)") --
    não está vazia, mas a correspondência exata depende de como
    `scrapping-prisma` popula `source_name`, o que este repositório não
    controla nem pode confirmar sozinho (ver PENDÊNCIA no README/relatório).

    Se `source_name` for nulo/vazio, ou se não houver correspondência exata,
    retorna `None` (a coluna é nullable) e loga um aviso -- nunca bloqueia o
    processamento da mensagem por causa disso."""
    if not source_name or not source_name.strip():
        return None

    normalized = source_name.strip()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id_midia FROM tabela_midia WHERE LOWER(nome_midia) = LOWER(%s)",
            (normalized,),
        )
        row = cur.fetchone()

    if row is None:
        logger.warning(
            "source_name %r não encontrado em tabela_midia (lookup exato case-insensitive) -- "
            "gravando id_midia=NULL. Ver PENDÊNCIA sobre mapeamento source_name -> nome_midia.",
            normalized,
        )
        return None

    return row[0]


def has_existing_classification(conn: psycopg.Connection, *, article_id: int, pipeline_version: str) -> bool:
    """Checagem de idempotência ANTES de rodar o pipeline (segmentação +
    matching + léxico) -- evita reprocessar à toa uma redelivery de artigo já
    classificado. Substitui a antiga `already_processed` (que consultava
    `classification_runs`, tabela que não existe mais em `app_db2`).

    Diferença importante em relação ao schema antigo: aqui não existe mais o
    conceito de "run" que sempre grava uma linha por artigo processado,
    mesmo quando nenhuma entidade foi identificada -- `tabela_classificacao`
    só ganha linha quando há PELO MENOS UM (parágrafo, entidade) casado. Ou
    seja: se um artigo não menciona nenhuma entidade do escopo, esta função
    NUNCA vai encontrar linha para ele, e o pipeline será refeito (barato,
    já que não há entidade para casar) a cada redelivery -- comportamento
    diferente do schema antigo, mas sem risco de duplicar dado (não há dado
    para duplicar). Documentado como limitação conhecida, não seria correto
    inventar uma tabela de "run" só para resolver isso sem o dono do
    `app_db2` decidir se isso é desejável no schema compartilhado.

    Esta função só evita trabalho no caso comum (redelivery sequencial) --
    quem garante a idempotência de verdade, mesmo sob concorrência, é a
    constraint `uq_tabela_classificacao_idempotencia` via
    `ON CONFLICT ... DO NOTHING` em `persist_classification_result_v2`."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM tabela_classificacao WHERE id_artigo_scrape = %s AND pipeline_version = %s LIMIT 1",
            (article_id, pipeline_version),
        )
        return cur.fetchone() is not None


def persist_classification_result_v2(
    conn: psycopg.Connection,
    payload: ScraperPayload,
    classifications: list[ClassificationResult],
    *,
    id_midia: int | None,
    metodo_classificacao: str,
    pipeline_version: str,
) -> int:
    """Grava resultados de classificação em `tabela_classificacao` (System
    DB compartilhado `app_db2`).

    Uma linha por `ClassificationResult` (não existe mais o conceito de
    "run" agrupando várias classificações do mesmo artigo, como em
    `classification_runs`) -- branch por `entity_type`: `'politico'`
    preenche `nr_titulo_eleitoral_candidato` e deixa `nr_partido` NULL,
    `'partido'` faz o inverso (e converte `entity_id` para `int`, já que
    `tabela_partido.nr_partido`/`tabela_classificacao.nr_partido` são
    `integer`, enquanto `entidade_matching.nr_partido` -- origem de
    `entity_id` -- é `text`).

    `evidencia_match` grava `{"tipo": ..., "score": ..., "alias": ...}`
    (formato documentado em
    scripts/sql/002_tabela_classificacao_idempotencia_auditoria.sql).

    Idempotência: a constraint `uq_tabela_classificacao_idempotencia`
    (id_artigo_scrape, paragrafo_analisado, nr_titulo_eleitoral_candidato,
    nr_partido, pipeline_version) é quem garante que reentrega da mesma
    mensagem não duplica linha -- `ON CONFLICT ... DO NOTHING` em vez de
    capturar `UniqueViolation` (diferente do schema antigo): aqui cada linha
    já carrega sua própria chave de idempotência, não existe uma linha "de
    corrida" no nível do artigo inteiro para checar antes (como era
    `classification_runs`).

    `id_midia` é parâmetro explícito (ver `resolve_id_midia`, chamado pelo
    caller antes desta função) e pode ser `None` (coluna nullable), mas
    nesse caso a linha fica sem o FK para `tabela_midia` preenchido.

    Retorna quantas linhas foram de fato inseridas (pode ser menor que
    `len(classifications)` em caso de reentrega parcial). Não comita a
    transação -- quem chama decide quando (ver `src/message_handler.py`:
    o commit só acontece depois desta função retornar com sucesso, e a
    notificação ao Analyser só é publicada depois desse commit)."""
    inserted = 0
    with conn.transaction():
        with conn.cursor() as cur:
            for result in classifications:
                if result.entity_type == "politico":
                    nr_titulo_eleitoral_candidato: str | None = result.entity_id
                    nr_partido: int | None = None
                elif result.entity_type == "partido":
                    nr_titulo_eleitoral_candidato = None
                    nr_partido = int(result.entity_id)
                else:
                    raise ValueError(
                        f"entity_type {result.entity_type!r} inesperado (esperado 'politico' ou 'partido')"
                    )

                cur.execute(
                    """
                    INSERT INTO tabela_classificacao (
                        id_artigo_scrape, paragrafo_analisado, link_noticia,
                        nr_titulo_eleitoral_candidato, nr_partido, id_midia,
                        classificacao_sentimento, score_confianca,
                        data_publicacao_noticia, metodo_classificacao, pipeline_version, evidencia_match
                    ) VALUES (
                        %(id_artigo_scrape)s, %(paragrafo_analisado)s, %(link_noticia)s,
                        %(nr_titulo_eleitoral_candidato)s, %(nr_partido)s, %(id_midia)s,
                        %(classificacao_sentimento)s, %(score_confianca)s,
                        %(data_publicacao_noticia)s, %(metodo_classificacao)s, %(pipeline_version)s, %(evidencia_match)s
                    )
                    ON CONFLICT ON CONSTRAINT uq_tabela_classificacao_idempotencia DO NOTHING
                    """,
                    {
                        "id_artigo_scrape": payload.article_id,
                        "paragrafo_analisado": result.evidence_text,
                        "link_noticia": payload.canonical_link,
                        "nr_titulo_eleitoral_candidato": nr_titulo_eleitoral_candidato,
                        "nr_partido": nr_partido,
                        "id_midia": id_midia,
                        "classificacao_sentimento": result.category,
                        "score_confianca": result.confidence,
                        "data_publicacao_noticia": payload.published_at,
                        "metodo_classificacao": metodo_classificacao,
                        "pipeline_version": pipeline_version,
                        "evidencia_match": json.dumps(
                            {
                                "tipo": result.entity_match_type,
                                "score": result.entity_match_score,
                                "alias": result.entity_matched_alias,
                            }
                        ),
                    },
                )
                inserted += cur.rowcount

    return inserted
