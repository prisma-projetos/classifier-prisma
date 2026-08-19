from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

from main import load_settings, setup_logging
from notification_publisher import publish_pending_notifications
from rabbitmq_publisher import PublishError, RabbitMQPublisher
from storage import get_connection, run_migrations

BASE_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    """Publica no RabbitMQ todas as notificações pendentes de
    analyser_notifications disponíveis no momento, e sai -- roda uma vez
    por execução, agendável via cron (mesmo padrão de
    scrapping-prisma/src/publish_jobs.py). Não é um processo daemon --
    diferente de src/main.py (o consumidor), que precisa ser contínuo."""
    load_dotenv(BASE_DIR / ".env")

    settings = load_settings()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = get_connection()
    applied = run_migrations(conn)
    if applied:
        logger.info("Migrations aplicadas nesta execução: %s", ", ".join(applied))

    try:
        try:
            with RabbitMQPublisher() as publisher:
                result = publish_pending_notifications(conn, publisher)
        except PublishError:
            logger.exception("Não foi possível conectar ao RabbitMQ -- nenhuma notificação foi claimada nesta execução")
            return 1
    finally:
        conn.close()

    logger.info(
        "Publicação de notificações concluída: %d publicadas, %d falharam", result["published"], result["failed"]
    )
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
