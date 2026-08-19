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


def _exact_match(alias: str, paragraph: str) -> bool:
    pattern = r"\b" + re.escape(alias) + r"\b"
    return re.search(pattern, paragraph, re.IGNORECASE | re.UNICODE) is not None


def _fuzzy_match_score(alias: str, paragraph: str) -> float:
    """Compara o alias contra janelas de palavras do parágrafo (não a string
    inteira) -- `fuzz.partial_ratio` sobre o parágrafo inteiro casaria "Lula"
    como substring de "Lulalândia" com score 1.0, já que ele ignora fronteira
    de palavra. Aqui cada janela tem o mesmo número de palavras que o alias,
    então erro de digitação em "Lulla" ainda pontua alto contra o token
    "Lulla", mas "Lulalândia" (uma palavra só) não vira substring escondida
    de "Lula"."""
    alias_words = alias.split()
    window_size = len(alias_words)
    paragraph_words = _WORD_RE.findall(paragraph)
    if window_size == 0 or len(paragraph_words) < window_size:
        return 0.0

    alias_lower = alias.lower()
    best_score = 0.0
    for i in range(len(paragraph_words) - window_size + 1):
        window = " ".join(paragraph_words[i : i + window_size]).lower()
        score = fuzz.ratio(alias_lower, window) / 100.0
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
    entidade no mesmo parágrafo não geram matches duplicados."""
    matches: list[EntityMatch] = []

    for entity in entities:
        best: EntityMatch | None = None

        for alias in entity.all_match_candidates():
            if _exact_match(alias, paragraph):
                best = EntityMatch(entity_id=entity.id, match_type="exact", score=1.0, matched_alias=alias)
                break  # exato já é o melhor resultado possível -- não precisa continuar

            fuzzy_score = _fuzzy_match_score(alias, paragraph)
            if fuzzy_score >= fuzzy_threshold and (best is None or fuzzy_score > best.score):
                best = EntityMatch(entity_id=entity.id, match_type="fuzzy", score=fuzzy_score, matched_alias=alias)

        if best is not None:
            matches.append(best)

    return matches
