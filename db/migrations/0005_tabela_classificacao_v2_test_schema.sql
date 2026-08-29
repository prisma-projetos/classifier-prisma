-- RÉPLICA PARA TESTE do schema novo do System DB compartilhado (`app_db2`),
-- usada exclusivamente em `classifier_test` (banco isolado de teste --
-- ver tests/conftest.py). NÃO é a fonte da verdade do schema: `app_db2` é
-- gerenciado por outra pessoa da equipe via DBeaver, fora deste
-- repositório (ver classifier-prisma/context.md, seção 3, e ADR 0002) --
-- este arquivo só existe para permitir testar
-- `src/classification_store.py::persist_classification_result_v2` e
-- `resolve_id_midia` de ponta a ponta, isolado, sem depender de rede/VPN
-- até o `app_db2` real.
--
-- Estrutura confirmada por consulta direta em `app_db2`
-- (information_schema.columns + pg_constraint) em 2026-08-28 -- reproduzida
-- aqui com o mínimo necessário para os testes (colunas de
-- tabela_politico/tabela_partido além da chave primária são só as usadas
-- pelo classifier, não a lista completa de campos do TSE que a tabela real
-- tem). Se o schema real mudar (nova coluna, constraint diferente), este
-- arquivo pode ficar dessincronizado -- não há automação hoje ligando os
-- dois; mantido manualmente até haver um ponto único de verdade
-- compartilhado entre os schemas de teste e produção.
--
-- Seguro criar/dropar tabela aqui -- `classifier_test` nunca é o banco de
-- produção compartilhado.

CREATE TABLE IF NOT EXISTS tabela_midia (
    id_midia SERIAL PRIMARY KEY,
    nome_midia VARCHAR
);

CREATE TABLE IF NOT EXISTS tabela_politico (
    nr_titulo_eleitoral_candidato VARCHAR PRIMARY KEY,
    nm_candidato VARCHAR
);

CREATE TABLE IF NOT EXISTS tabela_partido (
    nr_partido INTEGER PRIMARY KEY,
    sg_partido VARCHAR
);

CREATE TABLE IF NOT EXISTS tabela_classificacao (
    id_classificacao SERIAL PRIMARY KEY,
    id_artigo_scrape BIGINT,
    paragrafo_analisado TEXT,
    link_noticia TEXT,
    nr_titulo_eleitoral_candidato VARCHAR
        REFERENCES tabela_politico(nr_titulo_eleitoral_candidato),
    nr_partido INTEGER
        REFERENCES tabela_partido(nr_partido),
    id_midia INTEGER
        REFERENCES tabela_midia(id_midia),
    classificacao_sentimento VARCHAR,
    score_confianca DOUBLE PRECISION,
    data_publicacao_noticia TIMESTAMP,
    registro_classificacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    metodo_classificacao VARCHAR,
    pipeline_version TEXT NOT NULL,
    evidencia_match JSONB,

    CONSTRAINT uq_tabela_classificacao_idempotencia
        UNIQUE (id_artigo_scrape, paragrafo_analisado, nr_titulo_eleitoral_candidato, nr_partido, pipeline_version)
);
