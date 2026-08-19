-- Resultado individual: uma linha por (parágrafo, entidade) identificada em
-- um `classification_run`. Esta é a superfície de leitura "estável e
-- intencional" pensada como contrato para o Analyser e a API consultarem
-- depois (ver classifier-prisma/context.md, seção 3.1) -- por isso vive
-- separada de qualquer estado interno/transitório do Classifier (que hoje
-- nem existe como tabela, já que o processamento é síncrono por mensagem).
--
-- Cobre o contrato mínimo pedido em context.md raiz seção 4 e
-- classifier-prisma/context.md seção 4: entidade, trecho/evidência,
-- categoria, confiança, modelo+versão (herdados de classification_runs via
-- run_id, não duplicados aqui) e o score do fuzzy match da entidade (para
-- auditoria de "confundiram a entidade").

CREATE TABLE IF NOT EXISTS classifications (
    id BIGSERIAL PRIMARY KEY,

    run_id BIGINT NOT NULL
        REFERENCES classification_runs(id)
        ON DELETE CASCADE,

    entity_id TEXT NOT NULL
        REFERENCES political_entities(id),

    -- Posição do parágrafo dentro do full_text segmentado (0-based) --
    -- permite reconstruir a ordem original e localizar o trecho sem
    -- precisar reprocessar o texto.
    paragraph_index INTEGER NOT NULL,

    -- Evidência: o parágrafo literal que sustenta a classificação (nunca só
    -- o rótulo -- context.md raiz, seção 4/10).
    evidence_text TEXT NOT NULL,

    category TEXT NOT NULL
        CHECK (category IN ('neutro', 'negativo', 'positivo')),

    -- Heurística v1 (ver src/lexicon.py e README) -- não é uma
    -- probabilidade calibrada, é |pos - neg| / (pos + neg) sobre as
    -- ocorrências de termos do léxico no parágrafo.
    confidence DOUBLE PRECISION NOT NULL,

    entity_match_type TEXT NOT NULL
        CHECK (entity_match_type IN ('exact', 'fuzzy')),

    -- Score do match de entidade nesta linha específica (0-1). 1.0 para
    -- match exato; para fuzzy, o ratio do rapidfuzz normalizado. Fica junto
    -- da evidência (não só em log) para permitir auditar uma contestação de
    -- "confundiram a entidade" sem precisar cruzar com arquivos de log.
    entity_match_score DOUBLE PRECISION NOT NULL,

    -- Apelido/trecho de config/entities.yaml que efetivamente casou --
    -- ajuda a auditoria a entender POR QUE a entidade foi identificada.
    entity_matched_alias TEXT NOT NULL,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Segunda barreira de idempotência, redundante com a UNIQUE de
    -- classification_runs (article_id, pipeline_version) -- protege contra
    -- um bug que insira duas vezes dentro da MESMA transação/run, não só
    -- contra redelivery da mensagem inteira.
    UNIQUE (run_id, entity_id, paragraph_index)
);

CREATE INDEX IF NOT EXISTS idx_classifications_entity ON classifications(entity_id);
CREATE INDEX IF NOT EXISTS idx_classifications_run ON classifications(run_id);
