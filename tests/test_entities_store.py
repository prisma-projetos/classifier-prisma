from __future__ import annotations

import pytest

import storage
from entities_store import load_active_entities_from_db

# Mesma lista configurada em config/settings.yaml (entity_scope.cargos).
# Duplicada aqui de propósito -- o teste deve continuar provando o
# comportamento da v1 mesmo que outro agente altere o YAML sem querer; se a
# intenção for mesmo mudar de escopo (v2), o teste tem que ser atualizado
# como parte dessa mudança, não silenciosamente seguir o YAML.
CARGOS_V1 = ["PRESIDENTE", "VICE-PRESIDENTE"]


@pytest.fixture()
def app_db2_conn():
    """Conexão real e SOMENTE LEITURA com `app_db2` (System DB compartilhado
    da plataforma Prisma). `entidade_matching`/`tabela_candidatura` são
    tabelas do schema novo, gerenciado por outro processo via DBeaver (ver
    classifier-prisma/context.md e data/annotation/pendencias-tecnicas.md)
    -- não fazem parte de `db/migrations/` e não existem no banco isolado
    de teste (`POSTGRES_TEST_DB`/`classifier_test`), então não dá pra usar
    a fixture `db_conn` de tests/conftest.py aqui.

    `.env` já aponta POSTGRES_HOST/POSTGRES_DB direto para `app_db2` (mesmo
    banco usado por src/main.py em produção -- ver comentário lá). Este
    teste só executa SELECT via `load_active_entities_from_db`; o rollback
    no teardown é defensivo (não deveria haver nada para desfazer)."""
    conn = storage.get_connection()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def test_v1_scope_traz_26_politicos_e_14_partidos(app_db2_conn):
    """Regressão do recorte de fase decidido em equipe (2026-08-28, ver
    pendencias-tecnicas.md, seção 'Mudança de escopo do projeto'): a v1 só
    processa presidente/vice-presidente e os partidos deles -- não o
    universo de ~1.393 entidades de `entidade_matching`. Números batem com
    a consulta manual feita direto no banco antes desta implementação: 26
    políticos (13 PRESIDENTE + 13 VICE-PRESIDENTE, join com
    `tabela_candidatura`) + 14 partidos distintos = 40 entidades. Se esse
    teste começar a falhar por causa de INSERT/UPDATE de dado real no TSE
    (nova candidatura, partido trocado), é esperado -- reconfirmar os
    números direto no banco antes de simplesmente ajustar o `assert`."""
    entities = load_active_entities_from_db(app_db2_conn, CARGOS_V1)

    politicos = [e for e in entities if e.entity_type == "politico"]
    partidos = [e for e in entities if e.entity_type == "partido"]

    assert len(politicos) == 26
    assert len(partidos) == 14
    assert len(entities) == 40
    # a query usa UNION ALL -- confirma que não há duplicata de id entre os
    # dois blocos (política/partido nunca deveriam colidir, mas o teste
    # prova em vez de assumir).
    assert len({e.id for e in entities}) == len(entities)


def test_v1_scope_inclui_partidos_ambiguos_de_title_case(app_db2_conn):
    """AVANTE e DEMOCRATA são 2 dos 7 partidos 'sigla = nome' que exigem
    Title Case no matching (`_CASE_SENSITIVE_ALIASES` em
    src/entity_matcher.py, item 3 de pendencias-tecnicas.md). Confirma que
    esses dois continuam presentes no recorte da v1 -- se sumissem daqui
    (ex.: por um erro no cast `nr_partido::integer` do JOIN), o matching
    desses partidos quebraria silenciosamente sem nenhum teste apontando a
    causa raiz."""
    entities = load_active_entities_from_db(app_db2_conn, CARGOS_V1)
    partidos_por_nome = {e.canonical_name: e for e in entities if e.entity_type == "partido"}

    for nome in ("AVANTE", "DEMOCRATA"):
        assert nome in partidos_por_nome, f"partido {nome!r} ausente do escopo v1"
        entity = partidos_por_nome[nome]
        assert nome in entity.all_match_candidates()


def test_v1_scope_exclui_cargos_fora_do_recorte(app_db2_conn):
    """Prova que o filtro é restritivo de verdade, não só 'não dá erro'.
    GOVERNADOR é um dos cargos fora de escopo confirmados em
    `entidade_matching.ds_cargo` (junto com SENADOR, VICE-GOVERNADOR etc.,
    ver pendencias-tecnicas.md). Se esses candidatos vazassem pro resultado
    da v1, os testes de contagem acima já falhariam -- este teste aponta a
    causa raiz especificamente (título eleitoral de um candidato a
    GOVERNADOR não pode aparecer nas entidades carregadas)."""
    entities = load_active_entities_from_db(app_db2_conn, CARGOS_V1)
    ids_no_escopo = {e.id for e in entities if e.entity_type == "politico"}

    with app_db2_conn.cursor() as cur:
        cur.execute(
            """
            SELECT nr_titulo_eleitoral_candidato
            FROM entidade_matching
            WHERE entity_type = 'politico' AND ativo = true AND ds_cargo = 'GOVERNADOR'
            """
        )
        titulos_governador = [row[0] for row in cur.fetchall()]

    assert titulos_governador, (
        "esperava candidatos a GOVERNADOR em entidade_matching para o teste "
        "ser válido -- se a tabela mudou e não há mais nenhum, o teste "
        "precisa de outro cargo de controle"
    )
    assert ids_no_escopo.isdisjoint(titulos_governador)
