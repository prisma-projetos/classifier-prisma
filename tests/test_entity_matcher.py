from __future__ import annotations

from entities_store import Entity
from entity_matcher import match_entities

LULA = Entity(id="lula", canonical_name="Luiz Inácio Lula da Silva", entity_type="politician", aliases=("Lula",))
PT = Entity(id="pt", canonical_name="Partido dos Trabalhadores", entity_type="party", aliases=("PT",))

ENTITIES = [LULA, PT]


def test_exact_match_by_alias():
    matches = match_entities("O presidente Lula anunciou uma nova medida econômica.", ENTITIES, fuzzy_threshold=0.85)

    assert len(matches) == 1
    assert matches[0].entity_id == "lula"
    assert matches[0].match_type == "exact"
    assert matches[0].score == 1.0


def test_exact_match_does_not_match_substring_inside_another_word():
    # "Lulalândia" não deveria casar com o alias "Lula" (checagem por
    # palavra, não substring solta).
    matches = match_entities("A Lulalândia é um bairro conhecido na cidade.", ENTITIES, fuzzy_threshold=0.85)

    assert matches == []


def test_fuzzy_match_above_threshold_is_accepted():
    # Erro de digitação plausível ("Lulla" em vez de "Lula") -- não bate
    # exato, mas deve passar pelo fuzzy com um threshold razoável.
    matches = match_entities("O Lulla participou do evento em Brasília ontem.", ENTITIES, fuzzy_threshold=0.75)

    assert len(matches) == 1
    assert matches[0].entity_id == "lula"
    assert matches[0].match_type == "fuzzy"
    assert matches[0].score >= 0.75


def test_fuzzy_match_below_threshold_is_rejected():
    # Threshold alto demais para o erro de digitação -- não deve gerar
    # nenhum match para "lula".
    matches = match_entities("O Lulla participou do evento em Brasília ontem.", ENTITIES, fuzzy_threshold=0.99)

    assert matches == []


def test_multiple_entities_in_same_paragraph_are_all_matched():
    matches = match_entities(
        "O governo de Lula e o PT reagiram às críticas da oposição nesta semana.",
        ENTITIES,
        fuzzy_threshold=0.85,
    )

    matched_ids = {m.entity_id for m in matches}
    assert matched_ids == {"lula", "pt"}


def test_entity_matched_at_most_once_per_paragraph():
    matches = match_entities(
        "Lula falou hoje. Lula também comentou sobre economia. Lula ainda mencionou saúde.",
        ENTITIES,
        fuzzy_threshold=0.85,
    )

    assert len(matches) == 1
    assert matches[0].entity_id == "lula"
