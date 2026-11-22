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


REPUBLICANOS = Entity(
    id="republicanos",
    canonical_name="REPUBLICANOS",
    entity_type="party",
    aliases=(),
)


def test_case_sensitive_alias_matches_title_case_in_text():
    # "Republicanos" em Title Case é como o partido brasileiro aparece no
    # texto real (validado contra data/raw/articles_202608191040.csv) --
    # comportamento correto precisa continuar casando.
    matches = match_entities(
        "O Republicanos confirmou apoio à candidatura na convenção regional.",
        [REPUBLICANOS],
        fuzzy_threshold=0.85,
    )

    assert len(matches) == 1
    assert matches[0].entity_id == "republicanos"
    assert matches[0].match_type == "exact"


def test_case_sensitive_alias_does_not_match_lowercase_common_word():
    # "republicanos" minúsculo no meio da frase é a palavra comum (ex.: "os
    # republicanos americanos"), não o partido -- era o bug (IGNORECASE
    # fixo casava isso), agora não deve mais casar.
    matches = match_entities(
        "Os republicanos americanos reagiram à decisão da Casa Branca.",
        [REPUBLICANOS],
        fuzzy_threshold=0.85,
    )

    assert matches == []


def test_case_sensitive_alias_does_not_match_uppercase_form():
    # CAIXA ALTA não é como o texto real escreve o partido -- não deve
    # casar, mesmo sendo a forma como o alias vem do TSE.
    matches = match_entities(
        "O REPUBLICANOS divulgou nota oficial sobre a filiação.",
        [REPUBLICANOS],
        fuzzy_threshold=0.85,
    )

    assert matches == []


def test_non_risky_alias_still_matches_case_insensitively():
    # Alias fora da lista de risco (ex.: "Lula") não pode ter o
    # comportamento alterado -- continua IGNORECASE normal.
    matches = match_entities("o presidente lula anunciou uma medida hoje.", ENTITIES, fuzzy_threshold=0.85)

    assert len(matches) == 1
    assert matches[0].entity_id == "lula"
    assert matches[0].match_type == "exact"


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


# Caso real reportado pela Bea (2026-08-30): alias cadastrado em
# `entidade_matching` sem acento (dado real do TSE, acentuação
# inconsistente), mas o texto jornalístico real escreve com acento --
# antes da correção, isso não casava nem no exato nem no fuzzy (a
# diferença de UMA letra acentuada já derruba `fuzz.ratio` abaixo do
# threshold de 85%: "flavio" vs "flávio" = 83,3%).
FLAVIO_BOLSONARO = Entity(
    id="flavio-bolsonaro",
    canonical_name="FLAVIO NANTES BOLSONARO",
    entity_type="politician",
    aliases=("FLAVIO NANTES BOLSONARO", "FLAVIO BOLSONARO"),
)


def test_exact_match_ignores_accent_missing_in_alias():
    matches = match_entities(
        "A nova pesquisa aponta empate técnico entre o senador Flávio Bolsonaro (PL) e o "
        "presidente Lula (PT) em um virtual segundo turno.",
        [FLAVIO_BOLSONARO],
        fuzzy_threshold=0.85,
    )

    assert len(matches) == 1
    assert matches[0].entity_id == "flavio-bolsonaro"
    assert matches[0].match_type == "exact"
    assert matches[0].score == 1.0


def test_fuzzy_match_ignores_accent_missing_in_alias():
    # Mesmo caso, mas forçando o caminho fuzzy: "Bolsonaro" sozinho não
    # está nos aliases, só a combinação com o primeiro nome -- um erro de
    # digitação real (falta a última letra) tem que continuar aceito
    # mesmo com o acento a mais no texto.
    matches = match_entities(
        "O senador Flávio Bolsonar (PL) participou do evento em Brasília.",
        [FLAVIO_BOLSONARO],
        fuzzy_threshold=0.85,
    )

    assert len(matches) == 1
    assert matches[0].entity_id == "flavio-bolsonaro"
    assert matches[0].match_type == "fuzzy"


def test_accent_folding_does_not_reopen_false_positive_of_word_boundary():
    # Confirma que dobrar acento não enfraquece a proteção de fronteira de
    # palavra já existente: "Lulalândia" (fictício) não pode virar match de
    # "Lula" só porque o "â" central foi dobrado pra "a".
    matches = match_entities(
        "A cidade fictícia de Lulalândia foi citada como exemplo em um estudo.",
        [Entity(id="lula", canonical_name="Lula", entity_type="politician", aliases=("Lula",))],
        fuzzy_threshold=0.85,
    )

    assert matches == []
