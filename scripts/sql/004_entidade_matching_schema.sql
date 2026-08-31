-- Schema de `entidade_matching`, replicado a partir do banco de
-- desenvolvimento (`prisma-dev`, ex-`app_db2`) pra ser criado em produção.
-- Extraído direto do banco real via information_schema/pg_constraint em
-- 2026-08-30 -- não é reconstrução de memória.
--
-- ATENÇÃO -- MUDOU EM RELAÇÃO À VERSÃO ANTERIOR DESTE SCRIPT: o Gustavo
-- (DBA) corrigiu `nr_partido` de `text` para `integer` e adicionou duas
-- foreign keys (2026-08-30). Isso significa que `entidade_matching` NÃO É
-- MAIS uma tabela isolada -- ela agora DEPENDE de `tabela_politico` e
-- `tabela_partido` já existirem em prod, com as linhas correspondentes
-- (senão o INSERT de qualquer entidade cujo `nr_titulo_eleitoral_candidato`
-- ou `nr_partido` não tenha correspondência nessas tabelas vai falhar por
-- violação de FK). Rode este script DEPOIS de `tabela_politico` e
-- `tabela_partido` estarem populadas em prod, nunca antes.

CREATE TABLE entidade_matching (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,
    nr_titulo_eleitoral_candidato TEXT,
    nr_partido INTEGER,
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
        ),

    -- Adicionadas pelo Gustavo (DBA), 2026-08-30 -- impedem "dado fantasma"
    -- (entidade sem candidato/partido real correspondente).
    CONSTRAINT fk_entidade_politico
        FOREIGN KEY (nr_titulo_eleitoral_candidato) REFERENCES tabela_politico(nr_titulo_eleitoral_candidato),
    CONSTRAINT fk_entidade_partido
        FOREIGN KEY (nr_partido) REFERENCES tabela_partido(nr_partido)
);

CREATE INDEX idx_entidade_matching_ativo
    ON entidade_matching (ativo)
    WHERE ativo;
