from __future__ import annotations

from dataclasses import dataclass

import entity_matcher
import segmentation
from entities_store import Entity
from lexicon import Lexicon, classify_paragraph
from payload import ScraperPayload


class InsufficientContentError(Exception):
    """full_text vazio ou sem parágrafos aproveitáveis depois da
    segmentação -- ver context.md raiz, seção 7.4 ("validar se o texto
    possui conteúdo suficiente"). Mapeado para nack sem requeue pelo
    consumidor, igual a um payload inválido."""


@dataclass(frozen=True)
class ClassificationResult:
    paragraph_index: int
    evidence_text: str
    entity_id: str
    category: str
    confidence: float
    entity_match_type: str
    entity_match_score: float
    entity_matched_alias: str


def run_pipeline(
    payload: ScraperPayload,
    entities: list[Entity],
    lexicon: Lexicon,
    *,
    fuzzy_threshold: float,
) -> list[ClassificationResult]:
    """Orquestra a classificação de um conteúdo: segmenta por parágrafo,
    identifica entidades mencionadas em cada parágrafo (exato + fuzzy) e
    classifica o teor do parágrafo via léxico. Função pura em relação a
    banco/fila -- não persiste nada, não conhece RabbitMQ (ver
    src/classification_store.py e src/message_handler.py para o resto do
    fluxo). Isso é o que permite testar o pipeline inteiro sem
    Postgres/broker.

    Um parágrafo sem nenhuma entidade identificada não gera resultado (não
    há o que classificar). Um parágrafo com duas entidades diferentes gera
    duas linhas de resultado -- ver limitação de granularidade em
    src/lexicon.py."""
    paragraphs = segmentation.segment(payload.content_type, payload.full_text)
    if not paragraphs:
        raise InsufficientContentError(
            f"nenhum parágrafo aproveitável em full_text (article_id={payload.article_id})"
        )

    results: list[ClassificationResult] = []
    for index, paragraph in enumerate(paragraphs):
        matches = entity_matcher.match_entities(paragraph, entities, fuzzy_threshold=fuzzy_threshold)
        if not matches:
            continue

        lexicon_result = classify_paragraph(paragraph, lexicon)

        for match in matches:
            results.append(
                ClassificationResult(
                    paragraph_index=index,
                    evidence_text=paragraph,
                    entity_id=match.entity_id,
                    category=lexicon_result.category,
                    confidence=lexicon_result.confidence,
                    entity_match_type=match.match_type,
                    entity_match_score=match.score,
                    entity_matched_alias=match.matched_alias,
                )
            )

    return results
