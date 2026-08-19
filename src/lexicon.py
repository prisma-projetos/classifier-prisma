from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


class LexiconValidationError(Exception):
    pass


@dataclass(frozen=True)
class Lexicon:
    """Compilado uma vez a partir de config/lexicon.yaml -- ver o próprio
    arquivo para o aviso completo sobre ser uma lista artesanal (baseline
    v1, não cientificamente validada)."""

    positive_re: re.Pattern
    negative_re: re.Pattern


def _compile_terms(terms: list[str]) -> re.Pattern:
    # \b...\b delimita por palavra (funciona com acentuação em Python 3,
    # \w é Unicode por padrão) -- evita, por exemplo, "aprova" casar dentro
    # de "reprovação". Termos mais longos primeiro na alternação para não
    # deixar um termo curto "roubar" o match de um mais específico.
    escaped = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    pattern = r"\b(?:" + "|".join(escaped) + r")\b"
    return re.compile(pattern, re.IGNORECASE | re.UNICODE)


def load_lexicon(path: Path) -> Lexicon:
    with open(path, encoding="utf-8") as f:
        raw_doc = yaml.safe_load(f)

    if not isinstance(raw_doc, dict):
        raise LexiconValidationError(f"{path}: documento raiz deve ser um mapeamento")

    positive = raw_doc.get("positive")
    negative = raw_doc.get("negative")
    if not isinstance(positive, list) or not positive:
        raise LexiconValidationError(f"{path}: chave 'positive' ausente ou vazia")
    if not isinstance(negative, list) or not negative:
        raise LexiconValidationError(f"{path}: chave 'negative' ausente ou vazia")

    return Lexicon(
        positive_re=_compile_terms(positive),
        negative_re=_compile_terms(negative),
    )


@dataclass(frozen=True)
class LexiconClassification:
    category: str  # 'neutro' | 'negativo' | 'positivo'
    confidence: float
    positive_hits: int
    negative_hits: int


def classify_paragraph(paragraph: str, lexicon: Lexicon) -> LexiconClassification:
    """Classificação heurística v1: conta ocorrências de termos positivos e
    negativos no parágrafo inteiro (case-insensitive, por palavra) e decide
    pela contagem maior.

    Confiança é heurística, não uma probabilidade calibrada:
    |positivos - negativos| / (positivos + negativos), 0 quando não há
    nenhum termo do léxico (resultado 'neutro' por ausência de evidência,
    não por avaliação real de neutralidade -- ver context.md raiz, seção 9,
    sobre não reduzir 'neutro' a mera ausência de palavras-chave; esta é uma
    limitação conhecida da baseline v1, documentada no README).

    Limitação importante: a classificação é por PARÁGRAFO, não por
    entidade -- se duas entidades diferentes aparecem no mesmo parágrafo,
    ambas recebem a mesma categoria/confiança nesta v1 (não há atribuição
    fina de qual trecho do parágrafo se refere a qual entidade). Fase 2
    (LSTM) é a candidata natural a resolver isso melhor."""
    positive_hits = len(lexicon.positive_re.findall(paragraph))
    negative_hits = len(lexicon.negative_re.findall(paragraph))

    total = positive_hits + negative_hits
    if total == 0:
        return LexiconClassification("neutro", 0.0, positive_hits, negative_hits)

    confidence = abs(positive_hits - negative_hits) / total

    if positive_hits > negative_hits:
        category = "positivo"
    elif negative_hits > positive_hits:
        category = "negativo"
    else:
        category = "neutro"

    return LexiconClassification(category, confidence, positive_hits, negative_hits)
