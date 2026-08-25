from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from entities_store import Entity


@dataclass(frozen=True)
class EntityMatch:
    entity_id: str
    match_type: str  # 'exact' | 'fuzzy'
    score: float  # 0.0-1.0
    matched_alias: str


_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Siglas de partido que são, ao mesmo tempo, palavra comum do português --
# match case-insensitive causaria falso positivo (ex.: "os republicanos
# americanos", cobertura de política internacional que já aparece no corpus
# real, casaria com o partido brasileiro Republicanos). Validado contra
# data/raw/articles_202608191040.csv (229 ocorrências reais de
# "Republicanos"): quando é o partido de verdade, o texto SEMPRE escreve em
# Title Case ("Republicanos", "Avante"), nunca em CAIXA ALTA nem em
# minúsculo isolado no meio da frase. AGIR e DEMOCRATA são mais ambíguos
# ("agir"/"democrata" também aparecem como palavra comum minúscula no meio
# de frase), mas exigir Title Case ainda reduz bastante o risco -- ver
# data/annotation/pendencias-tecnicas.md, item 3.
#
# A chave é sempre a forma normalizada (UPPER) do alias, porque a fonte de
# dados (TSE, via `entidade_matching`) guarda sigla de partido em CAIXA
# ALTA (ex.: "REPUBLICANOS") -- não dá pra confiar na capitalização de
# entrada pra decidir SE o alias precisa de tratamento especial. O valor é
# a forma Title Case correta, usada como o padrão comparado contra o texto,
# já que é assim que a sigla aparece no texto real.
_CASE_SENSITIVE_ALIASES: dict[str, str] = {
    "REPUBLICANOS": "Republicanos",
    "AVANTE": "Avante",
    "CIDADANIA": "Cidadania",
    "PODEMOS": "Podemos",
    "SOLIDARIEDADE": "Solidariedade",
    "AGIR": "Agir",
    "DEMOCRATA": "Democrata",
}


def _exact_match(alias: str, paragraph: str) -> bool:
    """Match exato por palavra (`\\b...\\b`), case-insensitive por padrão.

    Exceção: aliases em `_CASE_SENSITIVE_ALIASES` (siglas de partido que
    coincidem com palavra comum do português) exigem Title Case exato
    (case-sensitive) -- ver comentário acima do dicionário. Pra todo o
    resto (nomes de político, outros partidos), o comportamento continua
    IGNORECASE normal."""
    special_form = _CASE_SENSITIVE_ALIASES.get(alias.upper())
    if special_form is not None:
        pattern = r"\b" + re.escape(special_form) + r"\b"
        return re.search(pattern, paragraph, re.UNICODE) is not None

    pattern = r"\b" + re.escape(alias) + r"\b"
    return re.search(pattern, paragraph, re.IGNORECASE | re.UNICODE) is not None


def _fuzzy_match_score(alias: str, paragraph: str) -> float:
    """Compara o alias contra janelas de palavras do parágrafo (não a string
    inteira) -- `fuzz.partial_ratio` sobre o parágrafo inteiro casaria "Lula"
    como substring de "Lulalândia" com score 1.0, já que ele ignora fronteira
    de palavra. Aqui cada janela tem o mesmo número de palavras que o alias.

    Dentro de cada janela, o score é o MÍNIMO do `fuzz.ratio` palavra-a-palavra
    (mesma posição no alias e na janela), não o `fuzz.ratio` da janela inteira
    concatenada. Comparar a janela inteira deixa uma palavra-âncora longa do
    alias dominar o score de caracteres -- por exemplo, `fuzz.ratio("presidente
    lula", "presidente da")` dá 0.857, porque "presidente" (10 chars) pesa
    muito mais que "lula"/"da" (4 chars), então "presidente da Câmara" casaria
    falsamente com o alias "presidente Lula" mesmo sem citar Lula. Comparando
    palavra por palavra e usando o mínimo, essa mesma frase cai pro ratio
    baixo de "lula" vs "da" e é rejeitada, enquanto um erro de digitação real
    (ex.: "presidente Lulla") mantém as duas palavras com ratio alto e
    continua sendo aceito. Para alias de uma palavra só, isso reduz ao mesmo
    comportamento de antes."""
    alias_words = alias.split()
    window_size = len(alias_words)
    paragraph_words = _WORD_RE.findall(paragraph)
    if window_size == 0 or len(paragraph_words) < window_size:
        return 0.0

    alias_words_lower = [w.lower() for w in alias_words]
    best_score = 0.0
    for i in range(len(paragraph_words) - window_size + 1):
        window_words = paragraph_words[i : i + window_size]
        score = min(
            fuzz.ratio(alias_word, window_word.lower())
            for alias_word, window_word in zip(alias_words_lower, window_words)
        ) / 100.0
        if score > best_score:
            best_score = score

    return best_score


def match_entities(paragraph: str, entities: list[Entity], *, fuzzy_threshold: float) -> list[EntityMatch]:
    """Identifica quais entidades são mencionadas no parágrafo, via match
    exato (por palavra, case-insensitive) OU fuzzy (rapidfuzz, comparando
    janelas de palavras do parágrafo com o mesmo tamanho do alias -- não a
    string inteira), o que vier primeiro por entidade -- não é NER, é uma
    decisão consciente registrada em classifier-prisma/context.md, seção 9.

    `fuzzy_threshold` é 0-1 (ex.: 0.85) -- comparável a `entity_match_score`
    persistido em `classifications`. Um match fuzzy abaixo do threshold é
    descartado (não gera EntityMatch) -- não existe "match fraco demais mas
    registrado mesmo assim" nesta v1.

    Cada entidade contribui no máximo um EntityMatch por parágrafo, com o
    melhor apelido encontrado (maior score) -- múltiplas menções da mesma
    entidade no mesmo parágrafo não geram matches duplicados.

    Aliases em `_CASE_SENSITIVE_ALIASES` (ver `_exact_match`) NUNCA caem no
    fallback fuzzy: `_fuzzy_match_score` compara em minúsculo dos dois
    lados, então "republicanos" ou "REPUBLICANOS" bateriam com score 1.0
    ali mesmo com o exact match bloqueado -- reabriria exatamente o mesmo
    falso positivo que o Title Case exigido no exact match existe pra
    evitar."""
    matches: list[EntityMatch] = []

    for entity in entities:
        best: EntityMatch | None = None

        for alias in entity.all_match_candidates():
            if _exact_match(alias, paragraph):
                best = EntityMatch(entity_id=entity.id, match_type="exact", score=1.0, matched_alias=alias)
                break  # exato já é o melhor resultado possível -- não precisa continuar

            if alias.upper() in _CASE_SENSITIVE_ALIASES:
                continue  # sem fallback fuzzy pra esses -- ver docstring acima

            fuzzy_score = _fuzzy_match_score(alias, paragraph)
            if fuzzy_score >= fuzzy_threshold and (best is None or fuzzy_score > best.score):
                best = EntityMatch(entity_id=entity.id, match_type="fuzzy", score=fuzzy_score, matched_alias=alias)

        if best is not None:
            matches.append(best)

    return matches
