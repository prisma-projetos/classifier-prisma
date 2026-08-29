from __future__ import annotations

import json
import os
from dataclasses import dataclass

import pika
from pika.exceptions import AMQPError, NackError, UnroutableError

# NENHUM destes nomes foi confirmado com o DevOps ainda (diferente da fila
# de consumo do Scraper, essa sim já fechada -- ver src/rabbitmq_consumer.py
# e o README, seção "RabbitMQ de consumo"). São placeholders razoáveis para
# poder desenvolver e testar agora -- não assumir que estão corretos em
# produção sem confirmar antes (ver classifier-prisma/context.md, seção 3.1
# e 9).
#
# Este wrapper foi construído originalmente para o outbox descrito na ADR
# 0002 (`src/notify_analyser.py`, hoje órfão -- ver ADR 0003). Desde
# 2026-08-28 é reaproveitado por `src/analyser_notifier.py` para publicação
# SÍNCRONA (logo após persistir e comitar em `tabela_classificacao`, ver
# `src/message_handler.py`) -- a infraestrutura de conexão/publicação em si
# não mudou, só quem chama e quando.
DEFAULT_QUEUE = "classifier.notificacoes.queue"
DEFAULT_EXCHANGE = "classifier.saida.exchange"
DEFAULT_EXCHANGE_TYPE = "direct"


class PublishError(Exception):
    """Falha ao conectar ou publicar no RabbitMQ. `notification_publisher`
    captura isso e delega para `notifications_store.fail_notification`
    (retry já existente) -- não derruba a execução inteira por causa de uma
    notificação."""


@dataclass(frozen=True)
class RabbitMQConfig:
    host: str
    port: int
    user: str
    password: str
    vhost: str
    queue: str
    exchange: str
    # Não é usado para declarar nada (este publisher não declara topologia
    # -- ver docstring de RabbitMQPublisher). Mantido só como documentação
    # do tipo assumido (`direct`), útil se um dia isso virar um teste de
    # integração real ou a decisão de não declarar for revista.
    exchange_type: str
    routing_key: str
    durable: bool

    @classmethod
    def from_env(cls) -> "RabbitMQConfig":
        queue = os.environ.get("RABBITMQ_NOTIFIER_QUEUE", DEFAULT_QUEUE)
        durable = os.environ.get("RABBITMQ_NOTIFIER_DURABLE", "true").strip().lower() not in ("false", "0", "no", "")
        return cls(
            host=os.environ.get("RABBITMQ_HOST", "localhost"),
            port=int(os.environ.get("RABBITMQ_PORT", "5672")),
            user=os.environ.get("RABBITMQ_NOTIFIER_USER", "guest"),
            password=os.environ.get("RABBITMQ_NOTIFIER_PASSWORD", "guest"),
            vhost=os.environ.get("RABBITMQ_VHOST", "/"),
            queue=queue,
            exchange=os.environ.get("RABBITMQ_NOTIFIER_EXCHANGE", DEFAULT_EXCHANGE),
            exchange_type=os.environ.get("RABBITMQ_NOTIFIER_EXCHANGE_TYPE", DEFAULT_EXCHANGE_TYPE),
            routing_key=os.environ.get("RABBITMQ_NOTIFIER_ROUTING_KEY", queue),
            durable=durable,
        )


class RabbitMQPublisher:
    """Wrapper fino e síncrono sobre `pika.BlockingConnection` -- réplica
    literal do estilo de `rabbitmq_client.py` do scrapping-prisma (ver
    classifier-prisma/context.md, seção 3.1). Abre uma conexão por execução
    do publisher (script roda uma vez e sai, ver src/notify_analyser.py) e
    publica mensagens JSON persistentes quando a fila é durável.

    NÃO declara exchange, fila nem faz bind. Quando a topologia da
    notificação Classifier -> Analyser for confirmada e criada pelo DevOps,
    este publisher assume que ela já existe -- igual ao publisher do
    scrapping-prisma para a fronteira Scraper -> Classifier.

    Uso:
        with RabbitMQPublisher() as publisher:
            publisher.publish(payload)
    """

    def __init__(self, config: RabbitMQConfig | None = None):
        self._config = config or RabbitMQConfig.from_env()
        self._connection: pika.BlockingConnection | None = None
        self._channel = None

    def __enter__(self) -> "RabbitMQPublisher":
        try:
            self._connect()
        except PublishError:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _connect(self) -> None:
        cfg = self._config
        try:
            credentials = pika.PlainCredentials(cfg.user, cfg.password)
            parameters = pika.ConnectionParameters(
                host=cfg.host,
                port=cfg.port,
                virtual_host=cfg.vhost,
                credentials=credentials,
            )
            self._connection = pika.BlockingConnection(parameters)
            self._channel = self._connection.channel()
            self._channel.confirm_delivery()
        except AMQPError as exc:
            raise PublishError(f"Falha ao conectar ao RabbitMQ: {exc}") from exc

    def publish(self, payload: dict) -> None:
        if self._channel is None:
            raise PublishError("RabbitMQPublisher usado fora do context manager (canal não aberto)")
        cfg = self._config
        try:
            self._channel.basic_publish(
                exchange=cfg.exchange,
                routing_key=cfg.routing_key,
                body=json.dumps(payload, default=str).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2 if cfg.durable else 1,
                ),
                mandatory=True,
            )
        except UnroutableError as exc:
            raise PublishError(f"Mensagem não roteada no RabbitMQ (routing key/binding incorretos?): {exc}") from exc
        except NackError as exc:
            raise PublishError(f"Mensagem recusada (nack) pelo RabbitMQ: {exc}") from exc
        except AMQPError as exc:
            raise PublishError(f"Falha ao publicar mensagem no RabbitMQ: {exc}") from exc

    def close(self) -> None:
        if self._connection is not None and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None
