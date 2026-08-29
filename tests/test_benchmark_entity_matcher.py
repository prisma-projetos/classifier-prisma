"""Não testa o benchmark inteiro (é lento -- roda match_entities contra
~1.400 entidades sintéticas/reais -- e é uma ferramenta de análise pontual,
não parte do pipeline normal de testes). Cobre `generate_synthetic_entities`
(peça que, se tivesse um bug silencioso -- ex.: colisões de nome reduzindo a
contagem, ids duplicados -- invalidaria os números medidos sem ninguém
perceber) e `entities_from_entidade_matching_rows` (mapeamento linha do
banco -> `Entity` usado no cenário "escopo completo real", testado aqui SEM
banco -- linhas sintéticas no formato de `entidade_matching`)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_entity_matcher import (  # noqa: E402
    entities_from_entidade_matching_rows,
    generate_synthetic_entities,
)


def test_generates_exact_requested_count():
    entities = generate_synthetic_entities(100, 10, seed=1)

    assert len(entities) == 110
    assert sum(1 for e in entities if e.entity_type == "politician") == 100
    assert sum(1 for e in entities if e.entity_type == "party") == 10


def test_generates_full_target_volume_without_collisions():
    # Volume real citado na tarefa: 1.363 políticos + 30 partidos.
    entities = generate_synthetic_entities(1_363, 30, seed=42)

    assert len(entities) == 1_393
    assert len({e.id for e in entities}) == 1_393  # nenhum id duplicado


def test_is_deterministic_given_same_seed():
    first_run = generate_synthetic_entities(200, 20, seed=7)
    second_run = generate_synthetic_entities(200, 20, seed=7)

    assert first_run == second_run


def test_each_entity_has_valid_shape_for_match_entities():
    entities = generate_synthetic_entities(50, 5, seed=3)

    for entity in entities:
        assert entity.entity_type in {"politician", "party"}
        assert 2 <= len(entity.canonical_name.split()) <= 4
        assert 1 <= len(entity.aliases) <= 2
        assert all(alias.strip() for alias in entity.all_match_candidates())


# ---------------------------------------------------------------------------
# entities_from_entidade_matching_rows -- mapeamento linha de entidade_matching
# -> Entity, usado no cenário "escopo completo real" (sem filtro de cargo).
# Mesma regra de id que `entities_store.load_active_entities_from_db`, mas
# testada aqui sem precisar de conexão com app_db2.
# ---------------------------------------------------------------------------


def test_maps_politico_row_using_nr_titulo_as_id():
    rows = [("politico", "123456789012", None, "FULANO DE TAL", ["FULANO"])]

    entities = entities_from_entidade_matching_rows(rows)

    assert len(entities) == 1
    entity = entities[0]
    assert entity.id == "123456789012"
    assert entity.entity_type == "politico"
    assert entity.canonical_name == "FULANO DE TAL"
    assert entity.aliases == ("FULANO",)


def test_maps_partido_row_using_nr_partido_as_id():
    rows = [("partido", None, "13", "PT", ["PARTIDO DOS TRABALHADORES"])]

    entities = entities_from_entidade_matching_rows(rows)

    assert len(entities) == 1
    entity = entities[0]
    assert entity.id == "13"
    assert entity.entity_type == "partido"


def test_handles_null_aliases_as_empty_tuple():
    rows = [("politico", "1", None, "FULANO", None)]

    entities = entities_from_entidade_matching_rows(rows)

    assert entities[0].aliases == ()


def test_raises_on_unexpected_entity_type():
    rows = [("politician", "1", None, "FULANO", [])]  # valor do YAML legado, não da coluna real

    with pytest.raises(ValueError, match="entity_type"):
        entities_from_entidade_matching_rows(rows)


def test_raises_when_key_column_is_missing():
    rows = [("politico", None, None, "FULANO SEM TITULO", [])]

    with pytest.raises(ValueError, match="sem chave preenchida"):
        entities_from_entidade_matching_rows(rows)


def test_full_scope_ids_never_collide_between_politico_and_partido():
    # Mesmo valor usado como nr_titulo de um político e nr_partido de um
    # partido não deveria colidir -- reflete o mesmo assert que
    # tests/test_entities_store.py já faz para o escopo v1 (UNION ALL sem
    # duplicata de id entre os dois blocos).
    rows = [
        ("politico", "13", None, "FULANO", []),
        ("partido", None, "13", "PT", []),
    ]

    entities = entities_from_entidade_matching_rows(rows)

    ids_by_type = {(e.entity_type, e.id) for e in entities}
    assert ("politico", "13") in ids_by_type
    assert ("partido", "13") in ids_by_type
    assert len(entities) == 2
