-- Entidades políticas (políticos e partidos) conhecidas pelo Classifier.
--
-- Fonte da verdade para o CONTEÚDO (nome canônico, apelidos) é o arquivo
-- config/entities.yaml, versionado no repositório -- igual ao padrão já
-- usado pelo scrapping-prisma para search_terms.yaml. Esta tabela é uma
-- CÓPIA sincronizada (upsert idempotente via src/entities_store.py), cujo
-- propósito é dar um `id` estável para as tabelas de resultado (ver
-- 0003_classifications_schema.sql) referenciarem via foreign key -- sem
-- isso, cada classificação teria que carregar o nome/apelido inteiro em vez
-- de um identificador, e reprocessamento/auditoria ficariam mais frágeis.
--
-- `aliases` fica guardado aqui só para inspeção/depuração via SQL -- o
-- casamento de entidade em si (exato + fuzzy, ver context.md seção 3.1)
-- roda em memória a partir do YAML carregado pelo consumidor, não faz
-- SELECT nesta tabela por mensagem processada.

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TABLE IF NOT EXISTS political_entities (
    id TEXT PRIMARY KEY,

    canonical_name TEXT NOT NULL,

    entity_type TEXT NOT NULL
        CHECK (entity_type IN ('politician', 'party')),

    aliases JSONB NOT NULL DEFAULT '[]'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_political_entities_type ON political_entities(entity_type);

CREATE TRIGGER trg_political_entities_updated_at
    BEFORE UPDATE ON political_entities
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
