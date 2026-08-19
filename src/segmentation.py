from __future__ import annotations

import re

# Despacho simples por content_type -- de propósito NÃO é uma interface ou
# Strategy Pattern formal (ver classifier-prisma/context.md, seção 9): hoje
# só existe uma estratégia real (notícia, por parágrafo), porque é só o que
# o scraper produz. Um segundo content_type (ex.: post de rede social,
# unidade de segmentação diferente) se torna só mais uma entrada neste
# dict, com sua própria função -- sem precisar herdar de nada.


# `trafilatura` (extrator usado pelo scrapping-prisma) separa parágrafos com
# UMA quebra de linha só, não linha em branco -- `\n+` cobre os dois casos
# (funciona tanto pra "\n" simples quanto pra "\n\n" de linha em branco).
_PARAGRAPH_SPLIT_RE = re.compile(r"\n+")
_WHITESPACE_RE = re.compile(r"[ \t]+")

# Parágrafos mais curtos que isso são descartados -- normalmente lixo de
# formatação (linha em branco perdida, um "•" solto, etc.), não uma unidade
# de análise que valha a pena classificar.
MIN_PARAGRAPH_CHARS = 20


class UnsupportedContentTypeError(Exception):
    """Levantado quando o content_type do payload não tem estratégia de
    segmentação implementada. Comportamento intencional: nunca cair
    silenciosamente na estratégia de parágrafo para um tipo de conteúdo que
    não foi pensado para ela (ver context.md raiz, seção 19.1, item 10 --
    não preencher lacuna por conta própria)."""

    def __init__(self, content_type: str):
        self.content_type = content_type
        super().__init__(
            f"content_type '{content_type}' não tem estratégia de segmentação implementada "
            f"(suportado(s): {sorted(_SEGMENTERS)})"
        )


def _segment_news_article(full_text: str) -> list[str]:
    """Segmenta por parágrafo: blocos separados por uma ou mais quebras de
    linha (linha em branco OU quebra simples -- ver _PARAGRAPH_SPLIT_RE).
    Espaços/tabs internos são colapsados (mas o texto do parágrafo em si não
    é reescrito além disso -- a evidência persistida deve continuar
    reconhecível como o trecho original)."""
    raw_paragraphs = _PARAGRAPH_SPLIT_RE.split(full_text)
    paragraphs = []
    for raw in raw_paragraphs:
        collapsed = _WHITESPACE_RE.sub(" ", raw).strip()
        if len(collapsed) >= MIN_PARAGRAPH_CHARS:
            paragraphs.append(collapsed)
    return paragraphs


_SEGMENTERS = {
    "news_article": _segment_news_article,
}


def segment(content_type: str, full_text: str) -> list[str]:
    """Retorna a lista de parágrafos (na ordem original) para o
    content_type informado. Levanta UnsupportedContentTypeError para
    qualquer content_type sem estratégia implementada."""
    segmenter = _SEGMENTERS.get(content_type)
    if segmenter is None:
        raise UnsupportedContentTypeError(content_type)
    return segmenter(full_text)
