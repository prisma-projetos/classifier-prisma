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
# `config/entities.yaml` não é mais a fonte usada em produção -- entidades
# vêm de `entidade_matching` (ver `entities_store.load_active_entities_from_db`
# abaixo). O YAML continua existindo só para os scripts de
# benchmark/anotação (scripts/benchmark_entity_matcher.py,
# scripts/build_annotation_batches.py), que apontam pro próprio caminho.
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
        # NÃO chamar storage.run_migrations(conn) aqui. `.env` agora aponta
        # `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB` direto para `app_db2`
        # -- o System DB COMPARTILHADO definitivo da plataforma Prisma
        # (Analyser, frontend etc. também vão usar), gerenciado por outra
        # pessoa da equipe via DBeaver, não por este repositório. As
        # migrations em db/migrations/ são do schema ANTIGO do Classifier
        # (political_entities, classification_runs, classifications,
        # analyser_notifications) -- rodá-las aqui criaria essas tabelas
        # dentro do banco compartilhado, contaminando um schema que não é
        # mais nosso para gerenciar. `storage.run_migrations` continua
        # existindo (usado pelos testes, contra POSTGRES_TEST_DB, um banco
        # isolado) -- só não é mais chamado neste fluxo de produção. Ver
        # classifier-prisma/context.md e docs/adr/0002 para o histórico
        # (dono das migrations do System DB fica em aberto).

        # Recorte de fase da v1 (decisão da equipe, 2026-08-28) -- só
        # candidatos a presidente/vice-presidente e os partidos deles, não
        # o universo inteiro de `entidade_matching`. Ver
        # config/settings.yaml (`entity_scope.cargos`) e
        # data/annotation/pendencias-tecnicas.md para o motivo (não é
        # limitação técnica).
        cargos_em_escopo = settings["entity_scope"]["cargos"]
        try:
            entities = entities_store.load_active_entities_from_db(conn, cargos_em_escopo)
        except ValueError as exc:
            logger.error("Falha ao carregar entidades de entidade_matching: %s", exc)
            return 1
        logger.info(
            "Entidades carregadas de entidade_matching (ativo=true, cargos=%s): %d",
            cargos_em_escopo,
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
            # `handle_message` comita a conexão internamente (ver docstring
            # de `message_handler.handle_message` e ADR 0004) -- grava
            # resultado (`tabela_classificacao`) e notificação pendente
            # (`classifier_analyser_outbox`) na mesma transação, e só então
            # comita. Este closure não publica nada no RabbitMQ -- quem
            # drena o outbox e publica de verdade é `src/notify_analyser.py`,
            # rodando à parte (agendável via cron).
            message_handler.handle_message(
                message_conn,
                body,
                entities=entities,
                lexicon=lexicon,
                fuzzy_threshold=fuzzy_threshold,
                model_name=model_name,
                model_version=model_version,
            )
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
