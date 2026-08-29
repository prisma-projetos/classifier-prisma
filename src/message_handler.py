from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

import classification_store
import notifications_store
import pipeline
from analyser_notifier import build_notification_payload
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
    rows_inserted: int
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
    checagem de idempotência, pipeline de classificação e persistência --
    em `tabela_classificacao` (resultado) e em `classifier_analyser_outbox`
    (notificação pendente pro Analyser), as duas na MESMA transação. Não
    sabe nada sobre o consumo em si (ack/nack) -- só sinaliza sucesso
    (`HandleResult`) ou levanta `RejectedMessage`.

    Decisão da Bea (2026-08-29, ver ADR 0004 --
    docs/adr/0004-outbox-proprio-para-notificacao-analyser.md): esta função
    NÃO publica mais nada no RabbitMQ diretamente (isso superou a decisão
    anterior da ADR 0003, publicação direta e síncrona). Em vez disso, grava
    o payload de notificação pronto em `classifier_analyser_outbox` -- na
    mesma transação do INSERT em `tabela_classificacao`, ANTES do commit --
    e quem publica de verdade é um processo separado
    (`src/notify_analyser.py`, agendável via cron, ver
    `src/notification_publisher.py`). Isso garante que a notificação nunca
    se perde por o RabbitMQ estar fora do ar no exato momento em que o
    resultado é persistido: ou as duas gravações (resultado + notificação
    pendente) commitam juntas, ou nenhuma delas commita -- é essa a garantia
    que a publicação direta (ADR 0003) não tinha.

    `classification_store.persist_classification_result_v2` não comita por
    conta própria (ver docstring dela); o commit final desta transação é
    implícito, feito pelo chamador (`src/main.py`) ao fechar a conexão por
    mensagem -- na prática, a psycopg abre a transação já na primeira
    consulta desta função (`has_existing_classification`) e só a fecha
    quando algo chama `conn.commit()`/`conn.rollback()` explicitamente ou a
    conexão é encerrada; por isso este módulo chama `conn.commit()`
    explicitamente logo após as duas gravações, e nunca antes."""
    try:
        parsed = parse_payload(raw_body)
    except PayloadValidationError as exc:
        raise RejectedMessage(f"payload inválido: {exc}") from exc

    if classification_store.has_existing_classification(
        conn, article_id=parsed.article_id, pipeline_version=parsed.pipeline_version
    ):
        logger.info(
            "job %s (article_id=%s, pipeline_version=%s) já possui classificação -- ignorando reentrega",
            parsed.job_id,
            parsed.article_id,
            parsed.pipeline_version,
        )
        return HandleResult(rows_inserted=0, already_processed=True)

    try:
        classifications = pipeline.run_pipeline(parsed, entities, lexicon, fuzzy_threshold=fuzzy_threshold)
    except (UnsupportedContentTypeError, pipeline.InsufficientContentError) as exc:
        raise RejectedMessage(f"falha ao processar conteúdo: {exc}") from exc

    id_midia = classification_store.resolve_id_midia(conn, parsed.source_name)

    # Formato "nome-vVersão" (mesma convenção já usada em `pipeline_version`,
    # ex. "lexicon-baseline-v1") -- `tabela_classificacao` tem uma única
    # coluna `metodo_classificacao` (texto), diferente do schema antigo que
    # separava `model_name`/`model_version` em duas colunas.
    metodo_classificacao = f"{model_name}-v{model_version}"

    inserted = classification_store.persist_classification_result_v2(
        conn,
        parsed,
        classifications,
        id_midia=id_midia,
        metodo_classificacao=metodo_classificacao,
        pipeline_version=parsed.pipeline_version,
    )

    if inserted > 0:
        # Só enfileira notificação se algo novo foi de fato gravado -- uma
        # redelivery onde todas as linhas colidiram com ON CONFLICT
        # (inserted == 0) não gera notificação duplicada ao Analyser (ver
        # context.md raiz, seção 16 -- "reprocessar não pode... reenviar
        # evento duplicado"). A gravação abaixo acontece ANTES do commit,
        # na mesma transação do INSERT em tabela_classificacao (ver ADR
        # 0004) -- se algo falhar daqui até o commit, as duas gravações são
        # desfeitas juntas.
        payload = build_notification_payload(parsed, classifications)
        notifications_store.enqueue_notification(
            conn,
            id_artigo_scrape=parsed.article_id,
            pipeline_version=parsed.pipeline_version,
            payload=payload,
        )

    conn.commit()

    logger.info(
        "job %s (article_id=%s) processado: %d classificações gravadas",
        parsed.job_id,
        parsed.article_id,
        inserted,
    )
    return HandleResult(rows_inserted=inserted, already_processed=False)
