from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg
from psycopg import errors as pg_errors

from payload import ScraperPayload
from pipeline import ClassificationResult


@dataclass(frozen=True)
class PersistOutcome:
    already_processed: bool
    run_id: int | None


def already_processed(conn: psycopg.Connection, *, article_id: int, pipeline_version: str) -> bool:
    """Checagem de idempotência ANTES de rodar o pipeline (segmentação +
    matching + léxico) -- evita trabalho à toa quando a mensagem é uma
    redelivery de algo já persistido. Não é a única garantia: a constraint
    UNIQUE (article_id, pipeline_version) em classification_runs (ver
    0002_classification_runs_schema.sql) é quem garante de verdade, mesmo
    sob concorrência -- esta função só evita refazer trabalho no caso comum
    (redelivery sequencial, não concorrente)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM classification_runs WHERE article_id = %s AND pipeline_version = %s",
            (article_id, pipeline_version),
        )
        return cur.fetchone() is not None


def persist_classification_result(
    conn: psycopg.Connection,
    payload: ScraperPayload,
    classifications: list[ClassificationResult],
    *,
    model_name: str,
    model_version: str,
    fuzzy_threshold: float,
) -> PersistOutcome:
    """Grava classification_runs + classifications + analyser_notifications
    NA MESMA TRANSAÇÃO (ver classifier-prisma/context.md, seção 3.1) -- só
    depois de todas as gravações terem sucesso é que faz sentido o
    consumidor confirmar o ack no RabbitMQ (isso acontece fora desta
    função, em src/message_handler.py).

    Se `article_id`+`pipeline_version` já foi processado (corrida entre
    duas mensagens redelivered ao mesmo tempo -- caso raro dado prefetch=1
    e um único consumidor, mas não impossível), a UniqueViolation da
    constraint é capturada e tratada como sucesso idempotente: nada é
    duplicado, `already_processed=True` é devolvido para o chamador só
    confirmar o ack."""
    try:
        with conn.transaction():
            run_id = _insert_run(
                conn,
                payload,
                model_name=model_name,
                model_version=model_version,
                fuzzy_threshold=fuzzy_threshold,
            )
            _insert_classifications(conn, run_id, classifications)
            _insert_notification(conn, run_id, payload, classifications)
    except pg_errors.UniqueViolation:
        return PersistOutcome(already_processed=True, run_id=None)

    return PersistOutcome(already_processed=False, run_id=run_id)


def _insert_run(
    conn: psycopg.Connection,
    payload: ScraperPayload,
    *,
    model_name: str,
    model_version: str,
    fuzzy_threshold: float,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO classification_runs (
                job_id, article_id, pipeline_version, content_type,
                title, canonical_link, source_name, published_at,
                model_name, model_version, entity_match_threshold
            ) VALUES (
                %(job_id)s, %(article_id)s, %(pipeline_version)s, %(content_type)s,
                %(title)s, %(canonical_link)s, %(source_name)s, %(published_at)s,
                %(model_name)s, %(model_version)s, %(fuzzy_threshold)s
            )
            RETURNING id
            """,
            {
                "job_id": payload.job_id,
                "article_id": payload.article_id,
                "pipeline_version": payload.pipeline_version,
                "content_type": payload.content_type,
                "title": payload.title,
                "canonical_link": payload.canonical_link,
                "source_name": payload.source_name,
                "published_at": payload.published_at,
                "model_name": model_name,
                "model_version": model_version,
                "fuzzy_threshold": fuzzy_threshold,
            },
        )
        (run_id,) = cur.fetchone()
    return run_id


def _insert_classifications(conn: psycopg.Connection, run_id: int, classifications: list[ClassificationResult]) -> None:
    with conn.cursor() as cur:
        for result in classifications:
            cur.execute(
                """
                INSERT INTO classifications (
                    run_id, entity_id, paragraph_index, evidence_text,
                    category, confidence, entity_match_type, entity_match_score, entity_matched_alias
                ) VALUES (
                    %(run_id)s, %(entity_id)s, %(paragraph_index)s, %(evidence_text)s,
                    %(category)s, %(confidence)s, %(entity_match_type)s, %(entity_match_score)s, %(entity_matched_alias)s
                )
                """,
                {
                    "run_id": run_id,
                    "entity_id": result.entity_id,
                    "paragraph_index": result.paragraph_index,
                    "evidence_text": result.evidence_text,
                    "category": result.category,
                    "confidence": result.confidence,
                    "entity_match_type": result.entity_match_type,
                    "entity_match_score": result.entity_match_score,
                    "entity_matched_alias": result.entity_matched_alias,
                },
            )


def _insert_notification(
    conn: psycopg.Connection,
    run_id: int,
    payload: ScraperPayload,
    classifications: list[ClassificationResult],
) -> None:
    entity_ids = sorted({result.entity_id for result in classifications})
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO analyser_notifications (run_id, article_id, job_id, pipeline_version, entity_ids)
            VALUES (%(run_id)s, %(article_id)s, %(job_id)s, %(pipeline_version)s, %(entity_ids)s)
            """,
            {
                "run_id": run_id,
                "article_id": payload.article_id,
                "job_id": payload.job_id,
                "pipeline_version": payload.pipeline_version,
                "entity_ids": json.dumps(entity_ids),
            },
        )
