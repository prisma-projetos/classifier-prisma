from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

import classification_store
import pipeline
from entities_store import Entity
from lexicon import Lexicon
from payload import PayloadValidationError, parse_payload
from segmentation import UnsupportedContentTypeError

logger = logging.getLogger("classifier")


class RejectedMessage(Exception):
    """Payload inválido ou erro de processamento -- o consumidor (ver
    src/rabbitmq_consumer.py) mapeia isso para `basic_nack(requeue=False)`,
    sem lógica de retry própria (vai para a DLQ que o DevOps já configurou,
    ver classifier-prisma/context.md, seção 2)."""


@dataclass(frozen=True)
class HandleResult:
    run_id: int | None
    already_processed: bool


def handle_message(
    conn: psycopg.Connection,
    raw_body: bytes,
    *,
    entities: list[Entity],
    lexicon: Lexicon,
    fuzzy_threshold: float,
    model_name: str,
    model_version: str,
) -> HandleResult:
    """Processa uma mensagem da fila do scraper de ponta a ponta: parse,
    checagem de idempotência, pipeline de classificação e persistência
    transacional (resultado + outbox de notificação). Não sabe nada sobre
    RabbitMQ -- não faz ack/nack, só sinaliza sucesso (`HandleResult`) ou
    levanta `RejectedMessage`. Isso é o que permite testar todo o fluxo
    (inclusive idempotência) sem precisar de um broker real -- só do banco
    de teste isolado (ver tests/test_message_handler.py)."""
    try:
        parsed = parse_payload(raw_body)
    except PayloadValidationError as exc:
        raise RejectedMessage(f"payload inválido: {exc}") from exc

    if classification_store.already_processed(
        conn, article_id=parsed.article_id, pipeline_version=parsed.pipeline_version
    ):
        logger.info(
            "job %s (article_id=%s, pipeline_version=%s) já processado -- ignorando reentrega",
            parsed.job_id,
            parsed.article_id,
            parsed.pipeline_version,
        )
        return HandleResult(run_id=None, already_processed=True)

    try:
        classifications = pipeline.run_pipeline(parsed, entities, lexicon, fuzzy_threshold=fuzzy_threshold)
    except (UnsupportedContentTypeError, pipeline.InsufficientContentError) as exc:
        raise RejectedMessage(f"falha ao processar conteúdo: {exc}") from exc

    outcome = classification_store.persist_classification_result(
        conn,
        parsed,
        classifications,
        model_name=model_name,
        model_version=model_version,
        fuzzy_threshold=fuzzy_threshold,
    )

    if outcome.already_processed:
        # Corrida rara (duas entregas concorrentes do mesmo job) -- a
        # constraint UNIQUE pegou o que a checagem prévia não pegou.
        # Tratado como sucesso idempotente, não como erro.
        logger.info(
            "job %s (article_id=%s) processado concorrentemente -- ignorando duplicata",
            parsed.job_id,
            parsed.article_id,
        )
        return HandleResult(run_id=None, already_processed=True)

    logger.info(
        "job %s (article_id=%s) processado: %d classificações geradas",
        parsed.job_id,
        parsed.article_id,
        len(classifications),
    )
    return HandleResult(run_id=outcome.run_id, already_processed=False)
