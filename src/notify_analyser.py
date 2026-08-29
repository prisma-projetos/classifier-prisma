from __future__ import annotations

# Reativado em 2026-08-29 (ver docs/adr/0004-outbox-proprio-para-notificacao-analyser.md):
# volta a drenar um outbox transacional, agora `classifier_analyser_outbox`
# (tabela de uso INTERNO do Classifier dentro do `app_db2`, criada via
# scripts/sql/003_classifier_analyser_outbox.sql -- não pelas migrations
# deste diretório). Antes disso (2026-08-28 a 2026-08-29) este script ficou
# órfão, drenando `analyser_notifications`, tabela que só existia no schema
# ANTIGO do Classifier (db/migrations/0004) -- ver ADR 0003 para o histórico
# dessa fase intermediária.

import sys
from pathlib import Path

from dotenv import load_dotenv

from main import load_settings, setup_logging
from notification_publisher import publish_pending_notifications
from rabbitmq_publisher import PublishError, RabbitMQPublisher
from storage import get_connection

BASE_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    """Publica no RabbitMQ todas as notificações pendentes de
    `classifier_analyser_outbox` disponíveis no momento, e sai -- roda uma
    vez por execução, agendável via cron (mesmo padrão de
    scrapping-prisma/src/publish_jobs.py). Não é um processo daemon --
    diferente de src/main.py (o consumidor), que precisa ser contínuo.

    NÃO chama `storage.run_migrations` -- mesma decisão e mesmo motivo
    documentado em `src/main.py`: `.env` aponta para `app_db2`, o System DB
    COMPARTILHADO gerenciado por outra pessoa da equipe via DBeaver.
    `db/migrations/` é o schema ANTIGO e órfão do Classifier -- rodá-las
    aqui contaminaria um schema que não é mais nosso para gerenciar.
    `classifier_analyser_outbox` já existe em produção (criada manualmente,
    ver ADR 0004); em `classifier_test` ela vem de
    `db/migrations/0006_classifier_analyser_outbox_test_schema.sql`, aplicada
    só pelos testes (`tests/conftest.py`)."""
    load_dotenv(BASE_DIR / ".env")

    settings = load_settings()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = get_connection()
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
