-- RÉPLICA PARA TESTE de `classifier_analyser_outbox`, usada exclusivamente
-- em `classifier_test` (ver tests/conftest.py) -- mesmo raciocínio de
-- 0005_tabela_classificacao_v2_test_schema.sql: a tabela real vive em
-- `app_db2` (System DB compartilhado), criada manualmente pela Bea via
-- `scripts/sql/003_classifier_analyser_outbox.sql` (já rodado e confirmado
-- direto no banco, 2026-08-28) -- não faz parte das migrations aplicadas em
-- produção. Este arquivo só existe para permitir testar
-- `src/notifications_store.py` / `src/notification_publisher.py` /
-- `src/message_handler.py` (gravação do outbox na mesma transação da
-- classificação) isolado, sem depender de rede até o `app_db2` real.
--
-- DDL idêntico ao de scripts/sql/003_classifier_analyser_outbox.sql --
-- ver aquele arquivo para o raciocínio completo (idempotência, motivo do
-- prefixo `classifier_`, formato do payload). Ver também
-- docs/adr/0004-outbox-proprio-para-notificacao-analyser.md.
--
-- Seguro criar/dropar tabela aqui -- `classifier_test` nunca é o banco de
-- produção compartilhado.

CREATE OR REPLACE FUNCTION classifier_set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS classifier_analyser_outbox (
    id BIGSERIAL PRIMARY KEY,

    id_artigo_scrape BIGINT NOT NULL,
    pipeline_version TEXT NOT NULL,

    payload JSONB NOT NULL,

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

    UNIQUE (id_artigo_scrape, pipeline_version)
);

CREATE INDEX IF NOT EXISTS idx_classifier_analyser_outbox_pending_scheduled
    ON classifier_analyser_outbox (scheduled_at)
    WHERE status = 'pending';

DROP TRIGGER IF EXISTS trg_classifier_analyser_outbox_updated_at ON classifier_analyser_outbox;
CREATE TRIGGER trg_classifier_analyser_outbox_updated_at
    BEFORE UPDATE ON classifier_analyser_outbox
    FOR EACH ROW
    EXECUTE FUNCTION classifier_set_updated_at();
