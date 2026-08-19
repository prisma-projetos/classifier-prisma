from __future__ import annotations

import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

import entities_store
import message_handler
import storage
from lexicon import load_lexicon
from rabbitmq_consumer import ConsumerConfig, run_consumer

BASE_DIR = Path(__file__).resolve().parent.parent
ENTITIES_PATH = BASE_DIR / "config" / "entities.yaml"
LEXICON_PATH = BASE_DIR / "config" / "lexicon.yaml"
SETTINGS_PATH = BASE_DIR / "config" / "settings.yaml"


def load_settings() -> dict:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(log_path: str) -> logging.Logger:
    log_file = BASE_DIR / log_path
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()],
    )
    return logging.getLogger("classifier")


def main() -> int:
    """Entrypoint do consumidor -- PROCESSO CONTÍNUO (daemon), diferente do
    modelo "roda uma vez e sai" do scrapping-prisma. Isso é inerente à
    natureza da tarefa (consumir uma fila é um processo de longa duração,
    não um lote com início e fim) -- ver README, seção "Modelo de
    execução"."""
    load_dotenv(BASE_DIR / ".env")

    settings = load_settings()
    logger = setup_logging(settings["logging"]["log_path"])

    conn = storage.get_connection()
    try:
        applied = storage.run_migrations(conn)
        if applied:
            logger.info("Migrations aplicadas nesta execução: %s", ", ".join(applied))

        try:
            entities = entities_store.load_and_validate(ENTITIES_PATH)
        except entities_store.EntityValidationError as exc:
            logger.error("Configuração de entidades inválida:\n%s", "\n".join(exc.errors))
            return 1

        sync_result = entities_store.sync_entities(conn, entities)
        conn.commit()
        logger.info(
            "Sincronização de entidades: %d inseridas, %d atualizadas (%d no total)",
            sync_result.inserted,
            sync_result.updated,
            len(entities),
        )

        lexicon = load_lexicon(LEXICON_PATH)
    finally:
        conn.close()

    fuzzy_threshold = settings["entity_matching"]["fuzzy_threshold"]
    model_name = settings["model"]["name"]
    model_version = str(settings["model"]["version"])

    def on_message(body: bytes) -> None:
        # Uma conexão nova por mensagem -- simplicidade deliberada para um
        # processo de longa duração: evita lidar com conexão ociosa indo
        # abaixo (idle timeout do Postgres/pooler) entre mensagens
        # espaçadas, ao custo de reconectar a cada mensagem. Se o volume de
        # mensagens crescer a ponto de o custo de conexão pesar, isso é
        # candidato natural a pool de conexões -- não implementado agora
        # por não haver necessidade comprovada (ver context.md raiz, seção
        # 19.2).
        message_conn = storage.get_connection()
        try:
            message_handler.handle_message(
                message_conn,
                body,
                entities=entities,
                lexicon=lexicon,
                fuzzy_threshold=fuzzy_threshold,
                model_name=model_name,
                model_version=model_version,
            )
            # Sem isso, o resultado fica só na transação implícita da
            # conexão (aberta pela própria checagem de idempotência) e é
            # perdido silenciosamente quando a conexão fecha logo abaixo
            # sem commit -- persist_classification_result usa
            # `with conn.transaction()`, que vira SAVEPOINT (não BEGIN) já
            # havendo transação aberta, então não comita a conexão sozinho.
            message_conn.commit()
        finally:
            message_conn.close()

    consumer_config = ConsumerConfig.from_env()
    try:
        run_consumer(consumer_config, on_message, reject_exception=message_handler.RejectedMessage)
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário")

    return 0


if __name__ == "__main__":
    sys.exit(main())
