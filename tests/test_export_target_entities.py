"""Testa a FORMA do YAML gerado por `scripts/export_target_entities.py`
(consumido pelo `scrapping-prisma`, fora deste repositório -- ver
`docs/propostas-mudancas-scraper.md`, seção 2) -- schema, campos
obrigatórios, `cargo: null` para partido. Não depende de `app_db2`: usa
`Entity` sintética (mesmo espírito de
`tests/test_benchmark_entity_matcher.py`) e uma conexão/cursor falsos para
`load_cargo_by_titulo`, já que o dado real só existe em `app_db2` (ver
`tests/test_entities_store.py` para o teste de integração real desse
recorte)."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from entities_store import Entity  # noqa: E402

from export_target_entities import (  # noqa: E402
    SCHEMA_VERSION,
    build_export_document,
    build_export_entities,
    load_cargo_by_titulo,
)


class _FakeCursor:
    def __init__(self, rows: list[tuple]):
        self._rows = rows
        self.executed_with: tuple | None = None

    def execute(self, query: str, params: dict | None = None) -> None:
        self.executed_with = (query, params)

    def fetchall(self) -> list[tuple]:
        return self._rows

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False


class _FakeConnection:
    def __init__(self, rows: list[tuple]):
        self._rows = rows
        self.cursor_calls = 0

    def cursor(self) -> _FakeCursor:
        self.cursor_calls += 1
        return _FakeCursor(self._rows)


POLITICO = Entity(
    id="111111111111",
    canonical_name="FULANO DE TAL",
    entity_type="politico",
    aliases=("FULANO",),
)
PARTIDO = Entity(
    id="13",
    canonical_name="PT",
    entity_type="partido",
    aliases=("PARTIDO DOS TRABALHADORES",),
)


# ---------------------------------------------------------------------------
# load_cargo_by_titulo -- conexão/cursor falsos, sem app_db2 real
# ---------------------------------------------------------------------------


def test_load_cargo_by_titulo_maps_rows_to_dict():
    conn = _FakeConnection(rows=[("111111111111", "PRESIDENTE"), ("222", "VICE-PRESIDENTE")])

    result = load_cargo_by_titulo(conn, ["111111111111", "222"])

    assert result == {"111111111111": "PRESIDENTE", "222": "VICE-PRESIDENTE"}


def test_load_cargo_by_titulo_short_circuits_on_empty_list():
    conn = _FakeConnection(rows=[])

    result = load_cargo_by_titulo(conn, [])

    assert result == {}
    assert conn.cursor_calls == 0  # não bate no banco à toa


# ---------------------------------------------------------------------------
# build_export_entities -- forma de cada item
# ---------------------------------------------------------------------------


def test_politico_entry_has_all_required_fields_and_cargo():
    entries = build_export_entities([POLITICO], {"111111111111": "PRESIDENTE"})

    assert len(entries) == 1
    entry = entries[0]
    assert entry["entity_id"] == "111111111111"
    assert isinstance(entry["entity_id"], str)
    assert entry["entity_type"] == "politico"
    assert entry["cargo"] == "PRESIDENTE"
    assert entry["nome_principal"] == "FULANO DE TAL"
    assert entry["aliases"] == ["FULANO"]


def test_partido_entry_has_cargo_none():
    entries = build_export_entities([PARTIDO], {})

    assert len(entries) == 1
    entry = entries[0]
    assert entry["entity_type"] == "partido"
    assert entry["cargo"] is None  # vira `null` no YAML


def test_raises_when_politico_has_no_cargo_mapped():
    with pytest.raises(ValueError, match="ds_cargo"):
        build_export_entities([POLITICO], {})  # dict sem o id do político


def test_output_is_sorted_deterministically_by_type_and_name():
    zeta = Entity(id="2", canonical_name="ZETA PARTY", entity_type="partido", aliases=())
    alfa = Entity(id="1", canonical_name="ALFA PARTY", entity_type="partido", aliases=())

    entries = build_export_entities([zeta, alfa], {})

    assert [e["nome_principal"] for e in entries] == ["ALFA PARTY", "ZETA PARTY"]


# ---------------------------------------------------------------------------
# build_export_document -- envelope do arquivo
# ---------------------------------------------------------------------------


def test_document_has_expected_top_level_schema():
    document = build_export_document([POLITICO, PARTIDO], {"111111111111": "PRESIDENTE"})

    assert document["schema_version"] == SCHEMA_VERSION
    assert "entidade_matching" in document["source"]
    assert len(document["entities"]) == 2

    # generated_at precisa ser um timestamp ISO 8601 UTC de verdade, não só
    # uma string qualquer -- quem consome (scrapping-prisma) pode precisar
    # parsear isso.
    generated_at = document["generated_at"]
    assert generated_at.endswith("Z")
    datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")


def test_document_counts_match_scope_v1_expectation():
    """Regressão de forma equivalente à de `tests/test_entities_store.py`
    (26 políticos + 14 partidos) -- aqui só garante que a contagem por
    entity_type no documento final bate com a lista de entrada, não repete
    o teste de integração contra app_db2."""
    politicos = [
        Entity(id=str(i), canonical_name=f"POLITICO {i}", entity_type="politico", aliases=())
        for i in range(26)
    ]
    partidos = [
        Entity(id=str(i), canonical_name=f"PARTIDO {i}", entity_type="partido", aliases=())
        for i in range(14)
    ]
    cargo_by_titulo = {p.id: "PRESIDENTE" for p in politicos}

    document = build_export_document(politicos + partidos, cargo_by_titulo)

    entities = document["entities"]
    assert sum(1 for e in entities if e["entity_type"] == "politico") == 26
    assert sum(1 for e in entities if e["entity_type"] == "partido") == 14
    assert len(entities) == 40
