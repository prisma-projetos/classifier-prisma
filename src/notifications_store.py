from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class NotificationRef:
    id: int
    run_id: int
    article_id: int
    job_id: int
    pipeline_version: str
    entity_ids: list[str]


def claim_next_notification(conn: psycopg.Connection) -> NotificationRef | None:
    """Reserva a próxima notificação pendente, mesmo padrão de
    `claim_next_job` do scrapping-prisma (`FOR UPDATE SKIP LOCKED`,
    transação curta que comita antes de devolver -- ver
    src/jobs_store.py::claim_next_job lá para o raciocínio completo)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, run_id, article_id, job_id, pipeline_version, entity_ids
            FROM analyser_notifications
            WHERE status = 'pending'
            ORDER BY scheduled_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None

        notification_id, run_id, article_id, job_id, pipeline_version, entity_ids = row
        cur.execute(
            "UPDATE analyser_notifications SET status = 'processing', attempts = attempts + 1 WHERE id = %s",
            (notification_id,),
        )
    conn.commit()
    return NotificationRef(
        id=notification_id,
        run_id=run_id,
        article_id=article_id,
        job_id=job_id,
        pipeline_version=pipeline_version,
        entity_ids=entity_ids,
    )


def mark_notified(conn: psycopg.Connection, notification_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE analyser_notifications SET status = 'notified', notified_at = now() WHERE id = %s",
            (notification_id,),
        )
    conn.commit()


def fail_notification(conn: psycopg.Connection, notification_id: int, *, error: str, retry: bool = True) -> None:
    """Mesma política de retry de `fail_job` no scrapping-prisma: volta
    para 'pending' se ainda houver tentativas disponíveis, senão marca
    'failed' definitivamente. Sem backoff exponencial nem circuit breaker
    novo aqui -- reaproveita o padrão já validado (ver
    classifier-prisma/context.md, seção 3.1)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE analyser_notifications
            SET status = CASE
                    WHEN %(retry)s AND attempts < max_attempts THEN 'pending'
                    ELSE 'failed'
                END,
                error_message = %(error)s
            WHERE id = %(notification_id)s
            """,
            {"retry": retry, "error": error, "notification_id": notification_id},
        )
    conn.commit()


def build_notification_payload(notification: NotificationRef, *, schema_version: int) -> dict:
    """Payload leve publicado no RabbitMQ -- não duplica o resultado
    completo (já está em `classifications`), só o suficiente para o
    Analyser saber o que mudou e ir consultar o System DB (ver context.md
    raiz, seção 7.6)."""
    return {
        "schema_version": schema_version,
        "run_id": notification.run_id,
        "article_id": notification.article_id,
        "job_id": notification.job_id,
        "pipeline_version": notification.pipeline_version,
        "entities": notification.entity_ids,
    }
