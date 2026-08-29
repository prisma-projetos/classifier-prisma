from __future__ import annotations

import json
import re

import pytest

import message_handler
import notifications_store
from analyser_notifier import build_notification_payload
from entities_store import Entity
from lexicon import Lexicon

# Entidade e léxico mínimos, só o suficiente para o pipeline gerar pelo
# menos uma classificação real (não vazia) -- mesmo padrão inline usado em
# tests/test_entity_matcher.py, sem depender de config/entities.yaml ou
# config/lexicon.yaml reais.
#
# `id` aqui já é a chave do schema NOVO (`nr_titulo_eleitoral_candidato`,
# sempre string) -- diferente do slug artificial ("lula") usado antes da
# migração para `entidade_matching`. Precisa existir uma linha
# correspondente em `tabela_politico` (ver `_seed_politico` abaixo) por
# causa da foreign key de `tabela_classificacao`.
LULA = Entity(
    id="111222333444", canonical_name="Luiz Inácio Lula da Silva", entity_type="politico", aliases=("Lula",)
)
ENTITIES = [LULA]

LEXICON = Lexicon(
    positive_re=re.compile(r"\b(aprovou)\b", re.IGNORECASE),
    negative_re=re.compile(r"\b(rejeitou)\b", re.IGNORECASE),
)

MODEL_NAME = "lexicon-baseline"
MODEL_VERSION = "1"
FUZZY_THRESHOLD = 0.85

# Parágrafo único (sem linha em branco -- fica como um só parágrafo depois
# da segmentação), grande o suficiente para passar do MIN_PARAGRAPH_CHARS,
# mencionando a entidade (via alias "Lula") e um termo positivo do léxico
# ("aprovou"), para gerar exatamente uma linha de classificação.
DEFAULT_FULL_TEXT = (
    "O presidente Lula aprovou nesta semana uma nova medida econômica que promete "
    "impactar diretamente o orçamento federal para o próximo ano."
)

# Parágrafo que não menciona nenhuma entidade conhecida -- usado para provar
# o caso "zero classificações" (ver
# test_article_without_entity_match_generates_no_row_and_keeps_being_reprocessed).
NO_ENTITY_FULL_TEXT = (
    "O ministério anunciou nesta semana uma medida econômica que promete impactar "
    "diretamente o orçamento federal para o próximo ano, sem citar nomes."
)


def _seed_politico(conn, nr_titulo: str, nome: str = "Luiz Inácio Lula da Silva") -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tabela_politico (nr_titulo_eleitoral_candidato, nm_candidato) VALUES (%s, %s) "
            "ON CONFLICT (nr_titulo_eleitoral_candidato) DO NOTHING",
            (nr_titulo, nome),
        )
    conn.commit()


def _seed_midia(conn, nome_midia: str) -> int:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO tabela_midia (nome_midia) VALUES (%s) RETURNING id_midia", (nome_midia,))
        (id_midia,) = cur.fetchone()
    conn.commit()
    return id_midia


def _build_payload_bytes(**overrides) -> bytes:
    """Monta o JSON do payload do scraper (ver src/payload.py) com valores
    padrão válidos, sobrescrevendo só os campos passados em `overrides` --
    evita repetir o dict inteiro em cada teste."""
    payload = {
        "schema_version": 1,
        "job_id": 42,
        "article_id": 123,
        "pipeline_version": "lexicon-baseline-v1",
        "title": "Governo anuncia nova medida",
        "full_text": DEFAULT_FULL_TEXT,
        "canonical_link": "https://exemplo.com/noticia-123",
        "source_name": "Portal Exemplo",
        "published_at": "2026-08-01T10:00:00-03:00",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def _handle(conn, raw_body: bytes) -> message_handler.HandleResult:
    return message_handler.handle_message(
        conn,
        raw_body,
        entities=ENTITIES,
        lexicon=LEXICON,
        fuzzy_threshold=FUZZY_THRESHOLD,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
    )


def _fetch_outbox_row(conn):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id_artigo_scrape, pipeline_version, payload, status, attempts FROM classifier_analyser_outbox"
        )
        return cur.fetchone()


def test_full_flow_persists_in_tabela_classificacao_and_enqueues_outbox_notification(db_conn):
    # Cenário feliz ponta a ponta com o outbox (ADR 0004): pipeline roda,
    # linha é gravada em tabela_classificacao (id_midia resolvido via lookup
    # em tabela_midia), e uma notificação PENDENTE é enfileirada em
    # classifier_analyser_outbox -- na mesma transação, não publicada
    # diretamente no RabbitMQ (isso é responsabilidade de um processo
    # separado, ver tests/test_notification_publisher.py).
    _seed_politico(db_conn, LULA.id)
    id_midia_esperado = _seed_midia(db_conn, "Portal Exemplo")

    raw_body = _build_payload_bytes(article_id=101, pipeline_version="v1")

    result = _handle(db_conn, raw_body)

    assert result == message_handler.HandleResult(rows_inserted=1, already_processed=False)
    assert _count(db_conn, "tabela_classificacao") == 1

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT nr_titulo_eleitoral_candidato, nr_partido, id_midia, classificacao_sentimento, "
            "metodo_classificacao, pipeline_version FROM tabela_classificacao"
        )
        row = cur.fetchone()
    assert row == (LULA.id, None, id_midia_esperado, "positivo", "lexicon-baseline-v1", "v1")

    assert _count(db_conn, "classifier_analyser_outbox") == 1
    id_artigo_scrape, pipeline_version, payload, status, attempts = _fetch_outbox_row(db_conn)
    assert id_artigo_scrape == 101
    assert pipeline_version == "v1"
    assert status == "pending"
    assert attempts == 0
    assert payload["article_id"] == 101
    assert len(payload["classificacoes"]) == 1
    assert payload["classificacoes"][0]["entity_id"] == LULA.id


def test_redelivery_of_same_message_is_ignored_by_precheck_and_not_reenqueued(db_conn):
    # Cenário obrigatório (context.md, seção 10): reentrega da MESMA
    # mensagem (mesmo article_id + pipeline_version) não pode duplicar linha
    # em tabela_classificacao nem enfileirar notificação duplicada no
    # outbox.
    _seed_politico(db_conn, LULA.id)

    raw_body = _build_payload_bytes(article_id=101, pipeline_version="v1")

    first = _handle(db_conn, raw_body)
    assert first == message_handler.HandleResult(rows_inserted=1, already_processed=False)
    assert _count(db_conn, "tabela_classificacao") == 1
    assert _count(db_conn, "classifier_analyser_outbox") == 1

    second = _handle(db_conn, raw_body)
    assert second == message_handler.HandleResult(rows_inserted=0, already_processed=True)

    # Pega pela checagem prévia (has_existing_classification) -- nem chega a
    # rodar o pipeline de novo, então não enfileira de novo.
    assert _count(db_conn, "tabela_classificacao") == 1
    assert _count(db_conn, "classifier_analyser_outbox") == 1


def test_no_media_match_leaves_id_midia_null_without_blocking_processing(db_conn):
    # source_name sem correspondência em tabela_midia -> id_midia NULL, mas
    # a mensagem é processada normalmente (ver classification_store.resolve_id_midia).
    _seed_politico(db_conn, LULA.id)
    raw_body = _build_payload_bytes(source_name="Veículo Desconhecido Que Não Está Cadastrado")

    result = _handle(db_conn, raw_body)

    assert result.rows_inserted == 1
    with db_conn.cursor() as cur:
        cur.execute("SELECT id_midia FROM tabela_classificacao")
        (id_midia,) = cur.fetchone()
    assert id_midia is None


def test_media_lookup_is_case_insensitive(db_conn):
    _seed_politico(db_conn, LULA.id)
    id_midia_esperado = _seed_midia(db_conn, "Portal Exemplo")
    raw_body = _build_payload_bytes(source_name="PORTAL EXEMPLO")

    _handle(db_conn, raw_body)

    with db_conn.cursor() as cur:
        cur.execute("SELECT id_midia FROM tabela_classificacao")
        (id_midia,) = cur.fetchone()
    assert id_midia == id_midia_esperado


def test_outbox_write_failure_after_persisting_classification_rolls_back_everything(monkeypatch, db_conn):
    # Prova a garantia central do outbox (ADR 0004): resultado e notificação
    # pendente são gravados na MESMA transação. Aqui simulamos uma falha
    # DEPOIS do INSERT em tabela_classificacao (já executado dentro da
    # transação em aberto) e DEPOIS do próprio INSERT no outbox, mas ANTES
    # do commit final -- e confirmamos que NADA fica persistido em nenhuma
    # das duas tabelas. Sem essa garantia, poderíamos ter um resultado
    # persistido sem nunca notificar o Analyser -- exatamente o risco que
    # motivou esta mudança (ver ADR 0003, risco aceito e agora mitigado).
    _seed_politico(db_conn, LULA.id)
    raw_body = _build_payload_bytes()

    real_enqueue = notifications_store.enqueue_notification

    def _enqueue_then_fail(conn, **kwargs):
        real_enqueue(conn, **kwargs)
        raise RuntimeError("falha simulada entre a gravação do outbox e o commit")

    monkeypatch.setattr(notifications_store, "enqueue_notification", _enqueue_then_fail)

    with pytest.raises(RuntimeError):
        _handle(db_conn, raw_body)

    # Ninguém chamou commit() antes da exceção -- desfaz explicitamente para
    # a asserção refletir o estado real que outro processo (ex.: `src/main.py`
    # fechando a conexão) encontraria: nada commitado.
    db_conn.rollback()

    assert _count(db_conn, "tabela_classificacao") == 0
    assert _count(db_conn, "classifier_analyser_outbox") == 0


def test_article_without_entity_match_generates_no_row_and_keeps_being_reprocessed(db_conn):
    # Limitação conhecida e documentada (ver docstring de
    # classification_store.has_existing_classification): sem "run" no
    # schema novo, um artigo sem NENHUMA entidade identificada nunca grava
    # linha em tabela_classificacao -- logo a checagem prévia nunca vai
    # encontrar nada para ele, e ele é reprocessado a cada redelivery. Não é
    # regressão: não há duplicação de dado (não há dado nenhum), só
    # reprocessamento redundante e barato.
    raw_body = _build_payload_bytes(full_text=NO_ENTITY_FULL_TEXT)

    first = _handle(db_conn, raw_body)
    second = _handle(db_conn, raw_body)

    assert first == message_handler.HandleResult(rows_inserted=0, already_processed=False)
    assert second == message_handler.HandleResult(rows_inserted=0, already_processed=False)
    assert _count(db_conn, "tabela_classificacao") == 0
    assert _count(db_conn, "classifier_analyser_outbox") == 0


def test_malformed_json_is_rejected_without_side_effects(db_conn):
    with pytest.raises(message_handler.RejectedMessage):
        _handle(db_conn, b"{isso nao e json valido")

    assert _count(db_conn, "tabela_classificacao") == 0


def test_payload_missing_required_field_is_rejected_without_side_effects(db_conn):
    payload = json.loads(_build_payload_bytes())
    del payload["canonical_link"]
    raw_body = json.dumps(payload).encode("utf-8")

    with pytest.raises(message_handler.RejectedMessage):
        _handle(db_conn, raw_body)

    assert _count(db_conn, "tabela_classificacao") == 0


def test_build_notification_payload_uses_only_fields_tabela_classificacao_has(db_conn):
    # Teste de contrato leve: garante que o payload de notificação não
    # inventa campo -- só o que já está disponível a partir do payload do
    # scraper e do resultado do pipeline (ver PENDÊNCIA sobre validação
    # deste formato com o time do Analyser, docs/adr/0003).
    import pipeline
    from payload import parse_payload

    parsed = parse_payload(_build_payload_bytes(article_id=999))
    classifications = pipeline.run_pipeline(parsed, ENTITIES, LEXICON, fuzzy_threshold=FUZZY_THRESHOLD)

    notification = build_notification_payload(parsed, classifications)

    assert notification["article_id"] == 999
    assert notification["schema_version"] == 1
    assert notification["classificacoes"][0]["entity_id"] == LULA.id
    assert notification["classificacoes"][0]["entity_type"] == "politico"
    assert "evidence_text" not in notification["classificacoes"][0]
