from __future__ import annotations

import logging
import os
import signal
import time
from dataclasses import dataclass
from typing import Callable

import pika
from pika.exceptions import AMQPConnectionError, AMQPError, StreamLostError

logger = logging.getLogger("classifier")

# Nomes confirmados pelo DevOps (ver classifier-prisma/context.md, seção 3,
# e scrapping-prisma/src/rabbitmq_client.py -- mesmos valores usados lá como
# default do publisher). A ROUTING KEY não está confirmada -- ver aviso
# abaixo, mesmo tratamento dado no lado do scraper.
DEFAULT_QUEUE = "scraper.dados.queue"


@dataclass(frozen=True)
class ConsumerConfig:
    host: str
    port: int
    user: str
    password: str
    vhost: str
    queue: str
    prefetch_count: int
    reconnect_delay_seconds: float

    @classmethod
    def from_env(cls) -> "ConsumerConfig":
        return cls(
            host=os.environ.get("RABBITMQ_HOST", "localhost"),
            port=int(os.environ.get("RABBITMQ_PORT", "5672")),
            user=os.environ.get("RABBITMQ_CONSUMER_USER", "guest"),
            password=os.environ.get("RABBITMQ_CONSUMER_PASSWORD", "guest"),
            vhost=os.environ.get("RABBITMQ_VHOST", "/"),
            queue=os.environ.get("RABBITMQ_CONSUMER_QUEUE", DEFAULT_QUEUE),
            prefetch_count=int(os.environ.get("RABBITMQ_CONSUMER_PREFETCH_COUNT", "1")),
            reconnect_delay_seconds=float(os.environ.get("RABBITMQ_RECONNECT_DELAY_SECONDS", "5")),
        )


def run_consumer(
    config: ConsumerConfig,
    on_message: Callable[[bytes], None],
    *,
    reject_exception: type[Exception],
) -> None:
    """Processo contínuo (daemon) que consome `config.queue` até receber
    SIGINT/SIGTERM. NÃO declara/cria exchange, fila nem faz bind -- a
    topologia já existe, criada pelo DevOps (ver README).

    Para cada mensagem: chama `on_message(body)`. Se `on_message` levantar
    `reject_exception`, a mensagem é rejeitada sem reentrega (nack sem
    requeue -- vai para a DLQ configurada pelo DevOps, sem lógica de retry
    própria aqui). Qualquer outra exceção também vira nack sem requeue,
    tratada como erro de processamento inesperado -- nunca perde a mensagem
    silenciosamente, mas também nunca reentrega às cegas algo que já falhou
    uma vez (reentrega só acontece por queda de conexão, nunca por decisão
    deste código).

    Ack manual (`auto_ack=False`) e `prefetch_count` configurável (default
    1) -- um consumidor não puxa a próxima mensagem antes de terminar
    (commitar + ack) a atual.

    Se a conexão cair, reconecta com um delay fixo simples (backoff
    simples, sem lógica sofisticada) até `stop()` ser chamado (SIGINT/
    SIGTERM) ou o processo ser encerrado."""
    state = {"should_stop": False, "channel": None}

    def _handle_signal(signum, _frame):
        logger.info("Sinal %s recebido -- encerrando consumo graciosamente após a mensagem atual", signum)
        state["should_stop"] = True
        channel = state["channel"]
        if channel is not None and channel.is_open:
            # Interrompe start_consuming() sem abandonar a mensagem em
            # processamento -- chamado do mesmo thread que roda o loop do
            # pika (BlockingConnection não é thread-safe para chamadas de
            # fora do próprio loop de I/O).
            channel.stop_consuming()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    def _on_delivery(channel, method, _properties, body):
        try:
            on_message(body)
        except reject_exception as exc:
            logger.warning("Mensagem rejeitada (delivery_tag=%s): %s", method.delivery_tag, exc)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        except Exception:
            logger.exception("Erro inesperado ao processar mensagem (delivery_tag=%s)", method.delivery_tag)
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return

        channel.basic_ack(delivery_tag=method.delivery_tag)

    while not state["should_stop"]:
        connection = None
        try:
            credentials = pika.PlainCredentials(config.user, config.password)
            parameters = pika.ConnectionParameters(
                host=config.host,
                port=config.port,
                virtual_host=config.vhost,
                credentials=credentials,
            )
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            channel.basic_qos(prefetch_count=config.prefetch_count)
            channel.basic_consume(queue=config.queue, on_message_callback=_on_delivery, auto_ack=False)

            state["channel"] = channel
            logger.info("Consumindo fila '%s' (prefetch=%d)", config.queue, config.prefetch_count)
            channel.start_consuming()
        except (AMQPConnectionError, StreamLostError, AMQPError) as exc:
            if state["should_stop"]:
                break
            logger.error(
                "Conexão com RabbitMQ perdida (%s) -- reconectando em %.0fs",
                exc,
                config.reconnect_delay_seconds,
            )
            time.sleep(config.reconnect_delay_seconds)
        finally:
            state["channel"] = None
            if connection is not None and connection.is_open:
                connection.close()

    logger.info("Consumidor encerrado")
