from __future__ import annotations

import json
from dataclasses import dataclass

import psycopg

# Adaptado em 2026-08-29 (ver docs/adr/0004-outbox-proprio-para-notificacao-analyser.md)
# para apontar para `classifier_analyser_outbox` -- tabela de uso INTERNO do
# Classifier dentro do `app_db2` (System DB compartilhado), diferente da
# antiga `analyser_notifications` (schema próprio do Classifier, órfã desde
# a migração para `app_db2`, ver ADR 0003 e db/migrations/0004). A LÓGICA de
# claim/fail/mark_notified é a mesma já validada antes -- só a chave e a
# origem do payload mudaram:
#
#   - chave de idempotência: (id_artigo_scrape, pipeline_version), a mesma
#     que identifica "esse artigo já foi classificado" em
#     `tabela_classificacao` -- não mais `run_id` (não existe mais o
#     conceito de "run" no schema novo, ver classification_store.py).
#   - o payload já vem PRONTO da coluna `payload` (JSONB), gravado no
#     momento da classificação (`enqueue_notification`, chamada por
#     `src/message_handler.py` na mesma transação do INSERT em
#     `tabela_classificacao`) -- diferente do design antigo, que remontava
#     o payload em `build_notification_payload` só no momento de publicar.
#     Por isso este módulo não tem mais uma função `build_notification_payload`
#     -- quem monta o payload é `src/analyser_notifier.py::build_notification_payload`,
#     uma vez só, antes de gravar.


@dataclass(frozen=True)
class OutboxNotification:
    id: int
    id_artigo_scrape: int
    pipeline_version: str
    payload: dict


def enqueue_notification(
    conn: psycopg.Connection,
    *,
    id_artigo_scrape: int,
    pipeline_version: str,
    payload: dict,
) -> None:
    """Grava uma notificação pendente para o Analyser em
    `classifier_analyser_outbox`. Chamada por `src/message_handler.py` NA
    MESMA TRANSAÇÃO que `classification_store.persist_classification_result_v2`
    grava `tabela_classificacao` -- por isso, igual àquela função, NÃO
    comita por conta própria (quem decide quando comitar/rollback é quem
    chama, ver `handle_message`). É esse encadeamento -- persistir resultado
    e enfileirar notificação no mesmo commit-ou-nada -- que dá a garantia de
    entrega que a publicação direta e síncrona (ADR 0003) não tinha.

    Idempotência: `ON CONFLICT (id_artigo_scrape, pipeline_version) DO
    NOTHING` -- mesma chave usada por `has_existing_classification`; uma
    tentativa de gravar de novo para o mesmo artigo/pipeline_version (ex.:
    corrida entre duas mensagens, embora a pré-checagem já deva evitar isso
    no caso comum) não duplica linha no outbox."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO classifier_analyser_outbox (id_artigo_scrape, pipeline_version, payload)
            VALUES (%(id_artigo_scrape)s, %(pipeline_version)s, %(payload)s)
            ON CONFLICT (id_artigo_scrape, pipeline_version) DO NOTHING
            """,
            {
                "id_artigo_scrape": id_artigo_scrape,
                "pipeline_version": pipeline_version,
                "payload": json.dumps(payload, default=str),
            },
        )


def claim_next_notification(conn: psycopg.Connection) -> OutboxNotification | None:
    """Reserva a próxima notificação pendente, mesmo padrão de
    `claim_next_job` do scrapping-prisma (`FOR UPDATE SKIP LOCKED`,
    transação curta que comita antes de devolver -- ver
    src/jobs_store.py::claim_next_job lá para o raciocínio completo)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, id_artigo_scrape, pipeline_version, payload
            FROM classifier_analyser_outbox
            WHERE status = 'pending' AND scheduled_at <= now()
            ORDER BY scheduled_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None

        notification_id, id_artigo_scrape, pipeline_version, payload = row
        cur.execute(
            "UPDATE classifier_analyser_outbox SET status = 'processing', attempts = attempts + 1 WHERE id = %s",
            (notification_id,),
        )
    conn.commit()
    return OutboxNotification(
        id=notification_id,
        id_artigo_scrape=id_artigo_scrape,
        pipeline_version=pipeline_version,
        payload=payload,
    )


def mark_notified(conn: psycopg.Connection, notification_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE classifier_analyser_outbox SET status = 'notified', notified_at = now() WHERE id = %s",
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
            UPDATE classifier_analyser_outbox
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
