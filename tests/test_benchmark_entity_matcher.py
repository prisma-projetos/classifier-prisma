"""Não testa o benchmark inteiro (é lento -- roda match_entities contra
~1.400 entidades sintéticas -- e é uma ferramenta de análise pontual, não
parte do pipeline normal de testes). Cobre só `generate_synthetic_entities`,
que é a peça que, se tivesse um bug silencioso (ex.: colisões de nome
reduzindo a contagem, ids duplicados), invalidaria os números medidos sem
ninguém perceber."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_entity_matcher import generate_synthetic_entities  # noqa: E402


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
