-- Uma linha por conteúdo efetivamente processado pelo Classifier (uma
-- "execução do pipeline", ver context.md raiz seção 17). É a âncora de
-- rastreabilidade até o job de origem no scrapping-prisma -- este
-- Classifier nunca acessa o Postgres do scraper diretamente (ver
-- classifier-prisma/context.md, seção 3), então `job_id`/`article_id` aqui
-- são só referências externas (sem foreign key -- não há como validar
-- contra um banco que este serviço não acessa).
--
-- Idempotência: UNIQUE (article_id, pipeline_version) é a garantia central
-- pedida pela tarefa -- se a mesma mensagem for reentregue pelo RabbitMQ
-- (ex.: consumidor caiu depois do commit mas antes do ack), a tentativa de
-- inserir um segundo run para o mesmo (article_id, pipeline_version) falha
-- com UniqueViolation, e o consumidor trata isso como "já processado", só
-- confirma o ack sem duplicar nada (ver src/classification_store.py).
-- `job_id` também é UNIQUE isoladamente como segunda barreira (o scraper já
-- garante um job por artigo/pipeline_version, então os dois devem sempre
-- andar juntos -- ver 0006_analysis_jobs_schema.sql do scrapping-prisma).
--
-- Reprocessamento sem apagar histórico (context.md raiz, seção 19.6): um
-- reprocessamento de verdade usa outro `pipeline_version` -- a linha do
-- processamento anterior nunca é sobrescrita nem apagada, só passa a
-- conviver com a nova. Por isso não existe aqui uma máquina de estados
-- ('reprocessando', 'superseded' etc.) -- não haveria uso real para ela
-- ainda, e o context.md do projeto pede para não criar abstração sem
-- necessidade comprovada.

CREATE TABLE IF NOT EXISTS classification_runs (
    id BIGSERIAL PRIMARY KEY,

    job_id BIGINT NOT NULL,
    article_id BIGINT NOT NULL,
    pipeline_version TEXT NOT NULL,

    -- Tipo de conteúdo recebido, usado para escolher a estratégia de
    -- segmentação (ver src/segmentation.py). O payload do scraper hoje não
    -- envia esse campo -- assumimos 'news_article' como default até o
    -- contrato do payload ganhar um campo explícito (ver README,
    -- "Limitações conhecidas").
    content_type TEXT NOT NULL,

    title TEXT,
    canonical_link TEXT,
    source_name TEXT,
    published_at TIMESTAMPTZ,

    -- Modelo/versão usados nesta execução (rastreabilidade e
    -- reprodutibilidade, context.md raiz seções 13/16). Na v1, sempre
    -- 'lexicon-baseline' / '1' -- ver README sobre a fase 2 (LSTM).
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,

    -- Threshold de fuzzy matching (0-1) usado para casar entidades nesta
    -- execução -- registrado aqui (não só em config) para permitir auditar
    -- uma contestação futura de "confundiram a entidade" mesmo que o
    -- threshold configurado mude depois.
    entity_match_threshold DOUBLE PRECISION NOT NULL,

    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (article_id, pipeline_version),
    UNIQUE (job_id)
);

CREATE INDEX IF NOT EXISTS idx_classification_runs_article ON classification_runs(article_id);
