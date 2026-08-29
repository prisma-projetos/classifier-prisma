"""Testes de regressão do pré-filtro de candidatos de `src/entity_matcher.py`.

O pré-filtro existe só por performance (ver o bloco "Pré-filtro de candidatos"
no módulo): ele descarta, antes de pagar regex/fuzzy, os aliases que
comprovadamente não podem casar. O risco desse tipo de otimização não é ficar
lenta -- é descartar demais e sumir com um match legítimo, silenciosamente.

Os testes abaixo cobrem exatamente os pontos onde o pré-filtro pode errar:
alias com pontuação (as duas tokenizações divergem), alias afogado em ruído,
match fuzzy (que o pré-filtro não pode cortar), threshold nas bordas e
invalidação do índice em cache.

Arquivo separado de `tests/test_entity_matcher.py` de propósito: aquele cobre
a REGRA de casamento, este cobre a otimização.
"""
from __future__ import annotations

import entity_matcher
import pytest
from entities_store import Entity
from entity_matcher import match_entities

LULA = Entity(id="lula", canonical_name="Luiz Inácio Lula da Silva", entity_type="politician", aliases=("Lula",))


def ruido(n: int) -> list[Entity]:
    """Entidades que não têm nada a ver com os parágrafos dos testes -- é o
    que o pré-filtro precisa descartar sem levar junto o alvo verdadeiro."""
    return [
        Entity(
            id=f"ruido-{i}",
            canonical_name=f"Fulano Beltrano{i}",
            entity_type="politician",
            aliases=(f"Beltrano{i}", f"Deputado Beltrano{i}"),
        )
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _sem_indice_reaproveitado():
    """Cada teste começa sem índice em cache, para um teste não mascarar o
    outro através do cache de módulo."""
    entity_matcher.reset_alias_index_cache()
    yield
    entity_matcher.reset_alias_index_cache()


def test_alias_com_pontuacao_interna_ainda_casa_exato():
    # "Jean-Pierre" é o caso em que as duas tokenizações do alias divergem:
    # `alias.split()` dá ["jean-pierre"] (que nunca aparece como palavra do
    # parágrafo, porque o parágrafo é tokenizado por `\w+`), enquanto `\w+`
    # sobre o alias dá ["jean", "pierre"] (que aparecem). Um pré-filtro que
    # olhasse só o `split()` descartaria este alias e o match exato sumiria.
    jean = Entity(
        id="jean",
        canonical_name="Jean-Pierre Silva",
        entity_type="politician",
        aliases=("Jean-Pierre",),
    )

    matches = match_entities(
        "O deputado Jean-Pierre Silva votou contra o projeto na comissão.",
        [jean, *ruido(300)],
        fuzzy_threshold=0.85,
    )

    assert [m.entity_id for m in matches] == ["jean"]
    assert matches[0].match_type == "exact"


def test_alias_com_apostrofo_ainda_casa_exato():
    davila = Entity(id="davila", canonical_name="D'Ávila Costa", entity_type="politician", aliases=("D'Ávila",))

    matches = match_entities(
        "A vereadora D'Ávila Costa apresentou o requerimento nesta terça.",
        [davila, *ruido(300)],
        fuzzy_threshold=0.85,
    )

    assert [m.entity_id for m in matches] == ["davila"]


def test_match_exato_sobrevive_a_um_volume_grande_de_entidades_irrelevantes():
    # Regressão do cenário real da migração: ~1.400 entidades, das quais
    # praticamente nenhuma é citada no parágrafo. O pré-filtro tem que
    # descartar as irrelevantes e preservar a única que importa.
    matches = match_entities(
        "O presidente Lula anunciou uma nova medida econômica.",
        [*ruido(700), LULA, *ruido(700)],
        fuzzy_threshold=0.85,
    )

    assert [m.entity_id for m in matches] == ["lula"]
    assert matches[0].match_type == "exact"


def test_match_fuzzy_sobrevive_a_um_volume_grande_de_entidades_irrelevantes():
    # O caminho fuzzy é o que o pré-filtro mais poderia cortar por engano:
    # "Lulla" não é igual a "Lula", então depende do filtro aceitar palavras
    # PARECIDAS, não só idênticas.
    matches = match_entities(
        "O Lulla participou do evento em Brasília ontem.",
        [*ruido(700), LULA],
        fuzzy_threshold=0.75,
    )

    assert [m.entity_id for m in matches] == ["lula"]
    assert matches[0].match_type == "fuzzy"
    assert matches[0].score >= 0.75


@pytest.mark.parametrize("threshold", [0.60, 0.75, 0.85, 0.90, 0.95, 0.99])
def test_o_prefiltro_nao_muda_o_resultado_em_nenhum_threshold(threshold):
    # O corte do pré-filtro é calculado a partir do threshold. Se ele fosse
    # apertado demais (erro de 1 unidade, ou de ponto flutuante ao converter
    # 0-1 para 0-100), o efeito apareceria só em algum threshold específico.
    paragrafo = "O presidente Lulla anunciou uma nova medida econômica no Brasil."
    entidades = [LULA, *ruido(200)]

    com_ruido = match_entities(paragrafo, entidades, fuzzy_threshold=threshold)
    entity_matcher.reset_alias_index_cache()
    sem_ruido = match_entities(paragrafo, [LULA], fuzzy_threshold=threshold)

    assert [(m.entity_id, m.match_type, m.score) for m in com_ruido] == [
        (m.entity_id, m.match_type, m.score) for m in sem_ruido
    ]


def test_threshold_zero_nao_e_afetado_pelo_prefiltro():
    # Borda: threshold 0 aceita qualquer score, então NADA pode ser
    # descartado -- nem alias sem chance nenhuma. Comportamento preexistente,
    # o pré-filtro só não pode estragá-lo.
    simbolos = Entity(id="simbolos", canonical_name="Partido & Cia", entity_type="party", aliases=("&",))

    matches = match_entities("O evento reuniu especialistas ontem.", [LULA, simbolos], fuzzy_threshold=0.0)

    assert {m.entity_id for m in matches} == {"lula", "simbolos"}


def test_alias_sem_caractere_de_palavra_nao_quebra_o_indice():
    # Alias como "---" não gera nenhuma palavra `\w+`. O índice precisa
    # aguentar isso sem estourar e sem inventar match.
    esquisito = Entity(id="esquisito", canonical_name="---", entity_type="party", aliases=("&", "100%"))

    matches = match_entities("O presidente Lula falou sobre economia.", [LULA, esquisito], fuzzy_threshold=0.85)

    assert [m.entity_id for m in matches] == ["lula"]


def test_indice_em_cache_e_revalidado_quando_a_lista_de_entidades_muda():
    # O índice é reaproveitado entre chamadas. Se a MESMA lista for alterada
    # in-place (mesmo `id()`, conteúdo diferente), o cache não pode devolver
    # o índice velho e ignorar a entidade nova.
    entidades = [LULA]
    paragrafo = "O presidente Lula conversou com o deputado Jean-Pierre Silva."

    antes = match_entities(paragrafo, entidades, fuzzy_threshold=0.85)
    assert [m.entity_id for m in antes] == ["lula"]

    entidades.append(Entity(id="jean", canonical_name="Jean-Pierre Silva", entity_type="politician", aliases=()))
    depois = match_entities(paragrafo, entidades, fuzzy_threshold=0.85)

    assert [m.entity_id for m in depois] == ["lula", "jean"]


def test_listas_de_entidades_diferentes_nao_contaminam_uma_a_outra():
    # Chamadas alternando entre duas listas distintas (cenário de teste, e de
    # um processo que recarrega entidades) não podem reusar o índice errado.
    outra = [Entity(id="pt", canonical_name="Partido dos Trabalhadores", entity_type="party", aliases=("PT",))]
    paragrafo = "O PT e o presidente Lula participaram da reunião."

    for _ in range(3):
        assert [m.entity_id for m in match_entities(paragrafo, [LULA], fuzzy_threshold=0.85)] == ["lula"]
        assert [m.entity_id for m in match_entities(paragrafo, outra, fuzzy_threshold=0.85)] == ["pt"]
