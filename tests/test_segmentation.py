from __future__ import annotations

import pytest
from segmentation import UnsupportedContentTypeError, segment


def test_segments_news_article_by_paragraph():
    text = (
        "Primeiro parágrafo da notícia, grande o suficiente para contar.\n\n"
        "Segundo parágrafo, também grande o suficiente para ser considerado válido.\n\n"
        "Terceiro parágrafo final da notícia analisada neste teste."
    )

    paragraphs = segment("news_article", text)

    assert len(paragraphs) == 3
    assert paragraphs[0].startswith("Primeiro parágrafo")
    assert paragraphs[1].startswith("Segundo parágrafo")
    assert paragraphs[2].startswith("Terceiro parágrafo")


def test_discards_paragraphs_shorter_than_minimum():
    text = "Ok\n\nEste parágrafo tem tamanho suficiente para ser mantido no resultado final.\n\n \n\nFim."

    paragraphs = segment("news_article", text)

    # "Ok" e "Fim." são curtos demais (< MIN_PARAGRAPH_CHARS) e descartados,
    # assim como o parágrafo só com espaço em branco.
    assert paragraphs == ["Este parágrafo tem tamanho suficiente para ser mantido no resultado final."]


def test_collapses_internal_whitespace():
    text = "Parágrafo   com    espaços    e   tabs\tirregulares  para normalizar no teste."

    paragraphs = segment("news_article", text)

    assert paragraphs == ["Parágrafo com espaços e tabs irregulares para normalizar no teste."]


def test_single_newline_is_also_a_paragraph_boundary():
    # trafilatura (extrator do scrapping-prisma) separa parágrafos reais com
    # UMA quebra de linha só, não linha em branco -- ver
    # src/segmentation.py, _PARAGRAPH_SPLIT_RE. Sem isso, um artigo inteiro
    # vira "um parágrafo só", inviabilizando classificar cada trecho
    # separadamente (ex.: elogio numa parte, crítica em outra, sobre a
    # mesma entidade).
    text = (
        "Primeiro parágrafo real, vindo direto da extração do artigo.\n"
        "Segundo parágrafo, separado do primeiro por uma única quebra de linha."
    )

    paragraphs = segment("news_article", text)

    assert paragraphs == [
        "Primeiro parágrafo real, vindo direto da extração do artigo.",
        "Segundo parágrafo, separado do primeiro por uma única quebra de linha.",
    ]


def test_unsupported_content_type_raises_explicit_error():
    with pytest.raises(UnsupportedContentTypeError):
        segment("social_media_post", "Algum texto qualquer, não importa aqui.")
