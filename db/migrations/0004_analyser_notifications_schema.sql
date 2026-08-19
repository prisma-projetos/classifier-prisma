-- Outbox transacional da fronteira Classifier -> Analyser (ver
-- classifier-prisma/context.md, seção 3.1, e context.md raiz, seção 7.6).
--
-- Mesmo padrão já validado em analysis_jobs do scrapping-prisma
-- (0006_analysis_jobs_schema.sql / 0013_analysis_jobs_published_status.sql),
-- adaptado para ser gravado pela APLICAÇÃO (não por um trigger de banco,
-- já que aqui não há uma coluna de status "de fora" mudando -- é o próprio
-- código do Classifier que decide, depois de classificar, que uma
-- notificação precisa existir): a aplicação grava esta linha NA MESMA
-- transação que grava classification_runs/classifications
-- (src/classification_store.py) -- nunca publica direto no RabbitMQ logo
-- após persistir, para não perder a notificação silenciosamente se o
-- broker falhar entre os dois passos.
--
-- Um publisher separado (src/notify_analyser.py, rodando uma vez e saindo,
-- agendável via cron -- mesmo modelo de execução do publish_jobs.py do
-- scrapping-prisma) drena esta tabela com retry por attempts/max_attempts.
--
-- Payload é LEVE de propósito -- carrega só os identificadores das
-- entidades classificadas neste run, não duplica o resultado completo (que
-- já está em `classifications`). O Analyser consulta o System DB para
-- pegar o resto (ver context.md raiz, seção 7.6).

CREATE TABLE IF NOT EXISTS analyser_notifications (
    id BIGSERIAL PRIMARY KEY,

    run_id BIGINT NOT NULL
        REFERENCES classification_runs(id)
        ON DELETE CASCADE,

    article_id BIGINT NOT NULL,
    job_id BIGINT NOT NULL,
    pipeline_version TEXT NOT NULL,

    -- Lista dos ids de political_entities classificados neste run --
    -- suficiente para o Analyser saber o que mudou e ir consultar o System
    -- DB (ver context.md raiz, seção 7.6). JSONB em vez de tabela
    -- normalizada: é só um payload de saída, nunca é filtrado/agregado por
    -- SQL neste repositório.
    entity_ids JSONB NOT NULL,

    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (
            status IN (
                'pending',
                'processing',
                'notified',
                'failed',
                'cancelled'
            )
        ),

    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,

    scheduled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notified_at TIMESTAMPTZ,
    error_message TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Um run só precisa de uma notificação -- reforça, junto com a UNIQUE
    -- de classification_runs, que redelivery da mensagem do scraper não
    -- pode duplicar entrada no outbox.
    UNIQUE (run_id)
);

CREATE INDEX IF NOT EXISTS idx_analyser_notifications_pending_scheduled
    ON analyser_notifications(scheduled_at)
    WHERE status = 'pending';

CREATE TRIGGER trg_analyser_notifications_updated_at
    BEFORE UPDATE ON analyser_notifications
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
