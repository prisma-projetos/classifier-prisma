-- Schema de `entidade_matching`, replicado a partir do banco de
-- desenvolvimento (`prisma-dev`, ex-`app_db2`) pra ser criado em produção.
-- Extraído direto do banco real via information_schema/pg_constraint em
-- 2026-08-30 -- não é reconstrução de memória.
--
-- Tabela isolada: nenhuma FK pra outra tabela do schema compartilhado
-- (só duas CHECK constraints internas). Pode ser criada e populada sem
-- depender de tabela_candidatura/tabela_partido/tabela_politico já
-- existirem em prod.

CREATE TABLE entidade_matching (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    nr_titulo_eleitoral_candidato TEXT,
    nr_partido TEXT,
    nome_principal TEXT NOT NULL,
    aliases JSONB NOT NULL,
    sg_uf TEXT,
    ds_cargo TEXT,
    origem TEXT NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT chk_entidade_matching_type
        CHECK (entity_type = ANY (ARRAY['politico', 'partido'])),

    -- Político preenche nr_titulo e deixa nr_partido NULL; partido é o
    -- inverso -- nunca os dois preenchidos, nunca os dois vazios.
    CONSTRAINT chk_entidade_matching_ref
        CHECK (
            (entity_type = 'politico' AND nr_titulo_eleitoral_candidato IS NOT NULL AND nr_partido IS NULL)
            OR
            (entity_type = 'partido' AND nr_partido IS NOT NULL AND nr_titulo_eleitoral_candidato IS NULL)
        )
);

CREATE INDEX idx_entidade_matching_ativo
    ON entidade_matching (ativo)
    WHERE ativo;
