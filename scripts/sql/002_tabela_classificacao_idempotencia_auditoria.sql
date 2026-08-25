-- Adiciona idempotência (pipeline_version + constraint única) e auditoria
-- de match (evidencia_match) na tabela_classificacao (app_db2).
--
-- Dados atuais são fictícios/descartáveis (confirmado pela Bea, 2026-08-25)
-- -- por isso o TRUNCATE antes do ADD COLUMN NOT NULL, sem precisar de
-- backfill. Se um dia isso rodar contra tabela com dado real, NÃO copiar
-- direto: precisa de UPDATE de backfill entre o ADD COLUMN e o SET NOT NULL
-- (ver pendencias-tecnicas.md, item 2, para a versão seguindo esse caminho).

TRUNCATE TABLE tabela_classificacao;

ALTER TABLE tabela_classificacao
    ADD COLUMN pipeline_version TEXT NOT NULL,
    ADD COLUMN evidencia_match JSONB;

-- Garante que reprocessar a mesma mensagem não duplica linha: mesma
-- notícia + mesmo parágrafo + mesma entidade + mesma versão do pipeline
-- não pode se repetir. NULL em nr_titulo_eleitoral_candidato/nr_partido
-- não atrapalha -- só um dos dois é preenchido por linha (política tem
-- nr_partido NULL, partido tem nr_titulo NULL), a comparação de igualdade
-- continua funcionando certo pra cada tipo.
ALTER TABLE tabela_classificacao
    ADD CONSTRAINT uq_tabela_classificacao_idempotencia
    UNIQUE (id_artigo_scrape, paragrafo_analisado, nr_titulo_eleitoral_candidato, nr_partido, pipeline_version);

-- Formato esperado de evidencia_match (documentar aqui pra quem for
-- escrever o código que grava nela):
--   {"tipo": "exato" | "aproximado", "score": 0.0-1.0, "alias": "texto que bateu"}
