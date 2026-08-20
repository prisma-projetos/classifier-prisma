from __future__ import annotations

from entities_store import Entity
from entity_matcher import match_entities

LULA = Entity(id="lula", canonical_name="Luiz Inácio Lula da Silva", entity_type="politician", aliases=("Lula",))
PT = Entity(id="pt", canonical_name="Partido dos Trabalhadores", entity_type="party", aliases=("PT",))

ENTITIES = [LULA, PT]

LULA_COM_ALIAS_PRESIDENTE = Entity(
    id="lula",
    canonical_name="Luiz Inácio Lula da Silva",
    entity_type="politician",
    aliases=("Lula", "presidente Lula"),
)
LULA_APENAS_ALIAS_PRESIDENTE = Entity(
    id="lula",
    canonical_name="Luiz Inácio Lula da Silva",
    entity_type="politician",
    aliases=("presidente Lula",),
)
UNIAO_BRASIL = Entity(
    id="uniao-brasil",
    canonical_name="União Brasil",
    entity_type="party",
    aliases=("União Brasil", "Uniao Brasil"),
)


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


def test_fuzzy_multiword_alias_does_not_match_unrelated_word_after_shared_prefix():
    # "presidente da Petrobras" não deveria casar com o alias "presidente
    # Lula" só porque a primeira palavra é idêntica -- comparar a janela
    # inteira concatenada deixava "presidente" (palavra longa) dominar o
    # score de caracteres e mascarar que "da" não tem nada a ver com "Lula".
    matches = match_entities(
        "A presidente da Petrobras, Magda Chambriard, afirmou que a empresa vai investir mais.",
        [LULA_COM_ALIAS_PRESIDENTE],
        fuzzy_threshold=0.85,
    )

    assert matches == []


def test_fuzzy_multiword_alias_still_matches_real_typo():
    # "presidente Lulla" (erro de digitação plausível) ainda deve casar com
    # o alias "presidente Lula" -- o fix não pode virar rejeição geral de
    # aliases com múltiplas palavras. Entidade só com esse alias (sem
    # "Lula" isolado) pra isolar o comportamento do alias multi-palavra.
    matches = match_entities(
        "O presidente Lulla anunciou uma nova medida econômica.",
        [LULA_APENAS_ALIAS_PRESIDENTE],
        fuzzy_threshold=0.85,
    )

    assert len(matches) == 1
    assert matches[0].entity_id == "lula"
    assert matches[0].match_type == "fuzzy"
    assert matches[0].matched_alias == "presidente Lula"


def test_fuzzy_multiword_alias_does_not_match_generic_country_mention():
    # "no Brasil" não deveria casar com o alias "União Brasil" -- mesmo
    # problema de palavra-âncora ("Brasil") dominando o score da janela
    # inteira.
    matches = match_entities(
        "O evento aconteceu no Brasil e reuniu especialistas de vários países.",
        [UNIAO_BRASIL],
        fuzzy_threshold=0.85,
    )

    assert matches == []
