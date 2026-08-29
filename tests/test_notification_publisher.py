from __future__ import annotations

import notifications_store
from notification_publisher import publish_pending_notifications
from rabbitmq_publisher import PublishError

# Testes do publisher do outbox (ADR 0004 --
# docs/adr/0004-outbox-proprio-para-notificacao-analyser.md): drena
# `classifier_analyser_outbox`, com retry por tentativas e circuit breaker
# de falhas consecutivas. Nunca conecta em um broker RabbitMQ real -- usa um
# publisher fake injetável (mesmo padrão de `Publisher` Protocol em
# `notification_publisher.py`).


def _enqueue(conn, *, id_artigo_scrape: int, pipeline_version: str = "v1", payload: dict | None = None) -> None:
    notifications_store.enqueue_notification(
        conn,
        id_artigo_scrape=id_artigo_scrape,
        pipeline_version=pipeline_version,
        payload=payload or {"article_id": id_artigo_scrape, "classificacoes": []},
    )
    conn.commit()


def _outbox_row(conn, id_artigo_scrape: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts, notified_at, error_message FROM classifier_analyser_outbox "
            "WHERE id_artigo_scrape = %s",
            (id_artigo_scrape,),
        )
        return cur.fetchone()


class _FakePublisher:
    """Substituto injetável de `notification_publisher.Publisher` -- publica
    com sucesso, a menos que `id_artigo_scrape` do payload esteja em
    `fail_for` (simula falha só para artigos escolhidos, sem depender de um
    broker real)."""

    def __init__(self, *, fail_for: set[int] | None = None, always_fail: bool = False):
        self.fail_for = fail_for or set()
        self.always_fail = always_fail
        self.published: list[dict] = []

    def publish(self, payload: dict) -> None:
        if self.always_fail or payload.get("article_id") in self.fail_for:
            raise PublishError("RabbitMQ indisponível (simulado no teste)")
        self.published.append(payload)


def test_claim_next_notification_reserves_pending_row_and_marks_processing(db_conn):
    _enqueue(db_conn, id_artigo_scrape=1)

    notification = notifications_store.claim_next_notification(db_conn)

    assert notification is not None
    assert notification.id_artigo_scrape == 1
    assert notification.pipeline_version == "v1"
    assert notification.payload["article_id"] == 1

    status, attempts, notified_at, error_message = _outbox_row(db_conn, 1)
    assert status == "processing"
    assert attempts == 1
    assert notified_at is None
    assert error_message is None


def test_claim_next_notification_returns_none_when_nothing_pending(db_conn):
    assert notifications_store.claim_next_notification(db_conn) is None


def test_successful_publish_marks_row_as_notified(db_conn):
    _enqueue(db_conn, id_artigo_scrape=1)
    publisher = _FakePublisher()

    result = publish_pending_notifications(db_conn, publisher)

    assert result == {"published": 1, "failed": 0}
    assert publisher.published == [{"article_id": 1, "classificacoes": []}]
    status, attempts, notified_at, error_message = _outbox_row(db_conn, 1)
    assert status == "notified"
    assert attempts == 1
    assert notified_at is not None
    assert error_message is None


def test_fail_notification_with_retries_left_goes_back_to_pending_and_records_error(db_conn):
    # Teste direto em notifications_store (não via publish_pending_notifications):
    # o laço do publisher reclama SEMPRE a linha pendente mais antiga (não
    # há backoff -- fail_notification não empurra scheduled_at para o
    # futuro, decisão deliberada de não adicionar sofisticação, ver
    # notifications_store.py), então com uma única linha "ruim" o laço a
    # retenta repetidas vezes na mesma execução em vez de parar em
    # attempts=1 -- ver test_single_bad_notification_exhausts_retries...
    # abaixo. Por isso o comportamento "uma falha isolada volta pra
    # pending com attempts=1" é testado aqui no nível de
    # claim/fail diretamente.
    _enqueue(db_conn, id_artigo_scrape=1)
    notification = notifications_store.claim_next_notification(db_conn)

    notifications_store.fail_notification(db_conn, notification.id, error="RabbitMQ indisponível (simulado)")

    status, attempts, notified_at, error_message = _outbox_row(db_conn, 1)
    assert status == "pending"
    assert attempts == 1
    assert notified_at is None
    assert error_message == "RabbitMQ indisponível (simulado)"


def test_single_bad_notification_exhausts_retries_within_one_run_and_is_marked_failed(db_conn):
    # max_attempts default (3) é igual a MAX_CONSECUTIVE_FAILURES (3) --
    # com uma única linha sempre falhando, ambos os limites são atingidos
    # juntos numa execução só: o laço reclama a mesma linha 3 vezes (não há
    # outra linha pendente para tentar), esgota attempts e o circuit
    # breaker também dispara no mesmo instante.
    _enqueue(db_conn, id_artigo_scrape=1)
    publisher = _FakePublisher(always_fail=True)

    result = publish_pending_notifications(db_conn, publisher)

    assert result == {"published": 0, "failed": 3}
    status, attempts, notified_at, error_message = _outbox_row(db_conn, 1)
    assert status == "failed"
    assert attempts == 3
    assert notified_at is None
    assert "RabbitMQ indisponível" in error_message


def test_circuit_breaker_stops_before_row_exhausts_its_own_retry_budget(db_conn):
    # Prova o circuit breaker como mecanismo PRÓPRIO, distinto de "a linha
    # esgotou suas tentativas": aqui a linha 1 tem max_attempts bem maior
    # que MAX_CONSECUTIVE_FAILURES (3), de propósito -- se o único limite em
    # jogo fosse max_attempts, a linha continuaria sendo retentada. O
    # circuit breaker interrompe a execução na 3ª falha CONSECUTIVA mesmo
    # assim, e as outras 4 linhas pendentes nem chegam a ser reclamadas
    # (permanecem intocadas, attempts=0) -- sinal de "RabbitMQ
    # provavelmente indisponível", não "essas notificações específicas têm
    # problema" (ver docstring de notification_publisher.py).
    _enqueue(db_conn, id_artigo_scrape=1)
    with db_conn.cursor() as cur:
        cur.execute("UPDATE classifier_analyser_outbox SET max_attempts = 10 WHERE id_artigo_scrape = 1")
    db_conn.commit()
    for article_id in range(2, 6):
        _enqueue(db_conn, id_artigo_scrape=article_id)
    publisher = _FakePublisher(always_fail=True)

    result = publish_pending_notifications(db_conn, publisher)

    assert result == {"published": 0, "failed": 3}

    status, attempts, notified_at, _error = _outbox_row(db_conn, 1)
    assert status == "pending"  # ainda tem tentativas disponíveis -- quem parou foi o circuit breaker
    assert attempts == 3
    assert notified_at is None

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT status, attempts FROM classifier_analyser_outbox WHERE id_artigo_scrape != 1 ORDER BY id_artigo_scrape"
        )
        others = cur.fetchall()
    assert others == [("pending", 0)] * 4


def test_idempotency_reentrega_do_mesmo_artigo_nao_duplica_linha_no_outbox(db_conn):
    # Mesma chave de idempotência de tabela_classificacao (id_artigo_scrape,
    # pipeline_version) -- reentrega da mesma mensagem do scraper não pode
    # duplicar linha no outbox (UNIQUE constraint + ON CONFLICT DO NOTHING).
    _enqueue(db_conn, id_artigo_scrape=1, pipeline_version="v1")
    _enqueue(db_conn, id_artigo_scrape=1, pipeline_version="v1")

    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM classifier_analyser_outbox WHERE id_artigo_scrape = 1 AND pipeline_version = 'v1'"
        )
        (count,) = cur.fetchone()
    assert count == 1


def test_different_pipeline_version_for_same_article_is_not_deduplicated(db_conn):
    # Reprocessamento de verdade (outro pipeline_version, ver context.md
    # raiz seção 19.6) não é bloqueado pela constraint de idempotência --
    # convive com o registro anterior.
    _enqueue(db_conn, id_artigo_scrape=1, pipeline_version="v1")
    _enqueue(db_conn, id_artigo_scrape=1, pipeline_version="v2")

    with db_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM classifier_analyser_outbox WHERE id_artigo_scrape = 1")
        (count,) = cur.fetchone()
    assert count == 2
