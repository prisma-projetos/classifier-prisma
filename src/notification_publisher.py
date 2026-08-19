from __future__ import annotations

import logging
from typing import Protocol

import notifications_store
from rabbitmq_publisher import PublishError

# Versionamento do payload de notificação -- mesmo raciocínio de
# jobs_publisher.SCHEMA_VERSION no scrapping-prisma: permite ao Analyser
# lidar com mudanças de contrato no futuro sem quebrar consumidores antigos.
SCHEMA_VERSION = 1

# Réplica literal do circuit breaker de jobs_publisher.py: N falhas de
# publicação SEGUIDAS são sinal muito mais forte de "o RabbitMQ está fora do
# ar" do que de "essa notificação específica tem problema" -- para cedo em
# vez de drenar a fila inteira contra um broker indisponível. Sem
# backoff exponencial nem qualquer sofisticação além disso (ver
# classifier-prisma/context.md, seção 3.1 -- reaproveitar, não melhorar
# especulativamente).
MAX_CONSECUTIVE_FAILURES = 3

logger = logging.getLogger("classifier")


class Publisher(Protocol):
    def publish(self, payload: dict) -> None: ...


def publish_pending_notifications(conn, publisher: Publisher) -> dict[str, int]:
    """Publica todas as notificações pendentes disponíveis no momento da
    chamada, uma de cada vez, e sai -- mesmo padrão de execução única de
    `src/notify_analyser.py` (agendável via cron, sem processo daemon).
    Reaproveita claim_next_notification/fail_notification, sem duplicar
    lógica de claim nem o retry por tentativas.

    Retorna a contagem de publicados/falhos desta execução (parcial, se
    parou cedo pelo circuit breaker)."""
    published = 0
    failed = 0
    consecutive_failures = 0

    while True:
        notification = notifications_store.claim_next_notification(conn)
        if notification is None:
            break

        payload = notifications_store.build_notification_payload(notification, schema_version=SCHEMA_VERSION)
        try:
            publisher.publish(payload)
        except PublishError as exc:
            logger.exception(
                "Falha ao publicar notificação %s (run %s) no RabbitMQ", notification.id, notification.run_id
            )
            notifications_store.fail_notification(conn, notification.id, error=str(exc))
            failed += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.error(
                    "Interrompendo execução: %d falhas consecutivas ao publicar -- "
                    "RabbitMQ provavelmente indisponível. Notificações restantes ficam "
                    "'pending' para a próxima execução agendada.",
                    consecutive_failures,
                )
                return {"published": published, "failed": failed}
            continue

        notifications_store.mark_notified(conn, notification.id)
        published += 1
        consecutive_failures = 0
        logger.info("Notificação %s (run %s) publicada no RabbitMQ", notification.id, notification.run_id)

    return {"published": published, "failed": failed}
