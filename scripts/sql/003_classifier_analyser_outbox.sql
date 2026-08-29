-- Outbox transacional da fronteira Classifier -> Analyser, mesmo padrão já
-- validado em `analysis_jobs` do scrapping-prisma (ver
-- classifier-prisma/docs/adr/0001-scraper-classifier-rabbitmq-outbox.md) e na
-- versão anterior deste mesmo mecanismo, quando o Classifier ainda tinha
-- banco próprio (db/migrations/0004_analyser_notifications_schema.sql,
-- órfã desde a migração pro System DB compartilhado).
--
-- Mitiga o risco aceito e documentado em
-- docs/adr/0003-notificacao-analyser-publicacao-direta.md: publicação
-- direta e síncrona perde a notificação silenciosamente se o RabbitMQ
-- estiver fora do ar bem na hora de publicar (a classificação em si NÃO se
-- perde -- só o aviso pro Analyser). Com esta tabela, a notificação fica
-- registrada como "pendente" ANTES de tentar publicar, e um publisher
-- separado (rodando à parte, agendável via cron -- mesmo modelo de execução
-- do publish_jobs.py do scrapping-prisma) drena o que não foi publicado,
-- com retry por attempts/max_attempts.
--
-- Roda dentro do `app_db2` (System DB compartilhado) por decisão explícita
-- da Bea (2026-08-28) -- não recriamos um banco privado só pra isso. Por
-- isso o nome é bem marcado com o prefixo `classifier_`: é uma tabela de
-- USO INTERNO do Classifier (só ele lê e escreve), não faz parte do modelo
-- de dados compartilhado (diferente de `tabela_classificacao`,
-- `entidade_matching` etc., que outros componentes também consultam) --
-- não segue a convenção `tabela_*` do restante do app_db2 de propósito, pra
-- não passar a impressão de que é uma tabela do domínio compartilhado.
--
-- Payload é gravado JÁ PRONTO (JSONB) no momento da escrita, não derivado
-- de novo no momento da publicação -- evita duplicar a lógica de montagem
-- de payload (`src/analyser_notifier.py::build_notification_payload`) em
-- dois lugares, e evita reconsultar `tabela_classificacao` (que já pode ter
-- mudado, por reentrega ou outro processamento) na hora de publicar.
--
-- IDEMPOTÊNCIA: UNIQUE(id_artigo_scrape, pipeline_version) -- mesmo par que
-- identifica "esse artigo já foi classificado" em `tabela_classificacao`
-- (ver scripts/sql/002_tabela_classificacao_idempotencia_auditoria.sql).
-- Reentrega da mesma mensagem do scraper não duplica linha aqui.

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

    -- Payload completo já montado (article_id, job_id, pipeline_version,
    -- link_noticia, data_publicacao_noticia, lista de classificações) --
    -- ver src/analyser_notifier.py::build_notification_payload. Formato
    -- ainda é PROPOSTA, não contrato fechado com o Analyser (ver ADR 0003).
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
