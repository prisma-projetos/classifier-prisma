# Classifier — Prisma

Componente responsável por classificar, individualmente, o teor de cada conteúdo coletado sobre políticos e partidos. Fica no meio do pipeline da plataforma Prisma:

```
Scraper (scrapping-prisma) --RabbitMQ--> Classifier (este repositório) --RabbitMQ--> Analyser
```

## Visão geral

O Classifier recebe um conteúdo já coletado (ex.: notícia), identifica quais políticos/partidos são mencionados, classifica o teor de cada trecho em relação a cada entidade (`neutro` / `negativo` / `positivo`), persiste o resultado no System DB e sinaliza o Analyser de que há resultado novo para agregar.

Este README cobre **como rodar o projeto**. Para responsabilidade detalhada, contrato de dados, regras de classificação, riscos e decisões em aberto, ver [`context.md`](context.md) (contexto deste repositório) e o `context.md` da raiz do Lab-inovação (fonte da verdade do produto).

Não é responsabilidade deste componente: coletar conteúdo, gerar análise agregada por entidade, expor API pública, apresentar resultado em interface, ou classificar viés político/ideológico (decisão de escopo do produto).

## Arquitetura resumida

### Dois modos de execução — não confundir

| | `src/main.py` | `src/notify_analyser.py` |
|---|---|---|
| O que faz | Consome a fila do Scraper, classifica e persiste | Publica notificações pendentes para o Analyser |
| Tipo de processo | Daemon (contínuo, fica rodando) | Roda uma vez e sai |
| Como operar | Mantido no ar (ex.: serviço/supervisor) | Agendado (ex.: cron) |

Motivo de serem dois processos separados: consumir fila é inerentemente contínuo; publicar o que está pendente na tabela de outbox é um lote com início e fim. Ver docstrings de ambos os arquivos para mais detalhe.

### Fluxo de classificação (pipeline)

Para cada mensagem consumida (um conteúdo):

1. **Segmentação** (`src/segmentation.py`) — separa o texto em parágrafos. Hoje só existe estratégia para `content_type = news_article`.
2. **Identificação de entidades** (`src/entity_matcher.py`) — para cada parágrafo, verifica quais políticos/partidos de `config/entities.yaml` são mencionados, por match exato (por palavra) ou fuzzy (rapidfuzz, contra janelas de palavras do mesmo tamanho do apelido). Não é NER.
3. **Classificação léxico/regra** (`src/lexicon.py`) — para cada parágrafo com pelo menos uma entidade identificada, conta ocorrências de termos positivos/negativos de `config/lexicon.yaml` e decide a categoria pela contagem maior. Essa é a baseline v1 — ver limitações abaixo.

O resultado é uma linha por (parágrafo, entidade) — `src/pipeline.py` é uma função pura, testável sem banco/fila.

### Persistência e integração — padrão outbox

Segue o padrão outbox transacional já decidido em ADR, nas duas fronteiras:

- **Scraper → Classifier**: consumo via RabbitMQ, sem acesso direto ao Postgres do scraper. Ver [ADR 0001](docs/adr/0001-scraper-classifier-rabbitmq-outbox.md).
- **Classifier → Analyser**: resultado e notificação pendente são gravados na mesma transação (`analyser_notifications`); um publisher separado (`src/notify_analyser.py`) drena essa tabela depois, com retry. Nome de fila/exchange dessa fronteira **ainda não confirmado com o DevOps** (placeholders no `.env.example`). Ver [ADR 0002](docs/adr/0002-system-db-compartilhado-outbox-analyser.md).

Em produção o Classifier não declara/cria topologia nenhuma no RabbitMQ (nem para consumo, nem para notificação) — isso é responsabilidade do DevOps. O RabbitMQ do `docker-compose.yml` é só para desenvolvimento local e já sobe com a topologia provisionada (ver [`docker/rabbitmq/README.md`](docker/rabbitmq/README.md)).

### Configuração versionada (`config/`)

- `config/entities.yaml` — lista curada manualmente de políticos e partidos conhecidos, com apelidos usados no match. Fonte da verdade do conteúdo; sincronizada para a tabela `political_entities` a cada execução do consumidor.
- `config/lexicon.yaml` — lista artesanal de termos positivos/negativos em PT-BR. Não é um léxico validado academicamente (não é SentiLex-PT nem OpLexicon) — é ponto de partida da baseline.
- `config/settings.yaml` — threshold do fuzzy matching, nome/versão do modelo gravados em cada execução, caminho do arquivo de log.

### Tabelas principais (`db/migrations/`)

- `political_entities` — cópia sincronizada de `config/entities.yaml`, dá `id` estável para as tabelas de resultado referenciarem.
- `classification_runs` — uma linha por conteúdo processado (uma execução do pipeline); garante idempotência via `UNIQUE (article_id, pipeline_version)`.
- `classifications` — resultado individual: uma linha por (parágrafo, entidade), com categoria, confiança e evidência textual.
- `analyser_notifications` — outbox da fronteira Classifier → Analyser, drenada por `src/notify_analyser.py`.

## Como rodar localmente

Pré-requisitos:

- Python 3.14 (confirmado no `.venv` deste repositório)
- Docker (para subir Postgres e RabbitMQ localmente via `docker-compose.yml`)

Passos:

```bash
# 1. Copiar o .env de exemplo e ajustar valores conforme necessário
cp .env.example .env

# 2. Subir Postgres e RabbitMQ locais
docker compose up -d

# 3. Criar/ativar um virtualenv e instalar dependências
python3.14 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. Rodar o consumidor (processo contínuo — fica no ar consumindo a fila do Scraper)
python src/main.py

# 5. Em outro momento/terminal, publicar notificações pendentes para o Analyser
# (roda uma vez e sai — em produção seria agendado via cron)
python src/notify_analyser.py
```

O consumidor (`src/main.py`) aplica migrations do System DB e sincroniza `config/entities.yaml` para o banco automaticamente ao subir — não é preciso rodar nada manualmente antes.

## Variáveis de ambiente

Todas descritas com comentário completo em [`.env.example`](.env.example). Resumo:

**Postgres (System DB)**
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — conexão com o banco compartilhado (Classifier grava resultado; Analyser e API o lerão depois).
- `POSTGRES_TEST_DB` — banco lógico separado usado pelos testes, mesmo servidor.

**RabbitMQ — consumo da fila do Scraper** (nomes já confirmados com o DevOps)
- `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_VHOST` — conexão com o broker.
- `RABBITMQ_MANAGEMENT_PORT` — porta da UI de management do RabbitMQ local (`docker-compose.yml`, container `rabbitmq:3-management`), só para desenvolvimento — inspecionar filas em `http://localhost:15672`. Não existe em produção.
- `RABBITMQ_CONSUMER_USER`, `RABBITMQ_CONSUMER_PASSWORD` — credenciais de consumo (defaults `guest/guest` são só o padrão local; credenciais reais de produção ainda não confirmadas com o DevOps).
- `RABBITMQ_CONSUMER_QUEUE` — fila de onde o Classifier consome os jobs do Scraper.
- `RABBITMQ_CONSUMER_PREFETCH_COUNT` — quantas mensagens não confirmadas o consumidor aceita por vez.
- `RABBITMQ_RECONNECT_DELAY_SECONDS` — intervalo de espera antes de tentar reconectar se a conexão cair.

**RabbitMQ — notificação para o Analyser** (nomes ainda NÃO confirmados com o DevOps — placeholders de desenvolvimento)
- `RABBITMQ_NOTIFIER_USER`, `RABBITMQ_NOTIFIER_PASSWORD` — credenciais de publicação.
- `RABBITMQ_NOTIFIER_EXCHANGE`, `RABBITMQ_NOTIFIER_EXCHANGE_TYPE`, `RABBITMQ_NOTIFIER_QUEUE`, `RABBITMQ_NOTIFIER_ROUTING_KEY`, `RABBITMQ_NOTIFIER_DURABLE` — topologia usada por `src/notify_analyser.py`. Não usar esses valores como definitivos em produção sem confirmar com o DevOps antes.

## Como testar

```bash
pytest
```

Os testes usam um banco de teste isolado (`POSTGRES_TEST_DB`), criado e com as migrations aplicadas automaticamente pelo `tests/conftest.py` — não é preciso preparar banco manualmente, só ter Postgres no ar (`docker compose up -d`) e o `.env` configurado. As tabelas de dados são limpas (`TRUNCATE`) antes de cada teste.

## Limitações conhecidas da baseline v1 (léxico/regra)

- **Granularidade por parágrafo, não por entidade**: se duas entidades diferentes aparecem no mesmo parágrafo, ambas recebem a mesma categoria e confiança — não há atribuição fina de qual trecho se refere a qual entidade.
- **Confiança é heurística, não probabilidade calibrada**: `|positivos - negativos| / (positivos + negativos)`, sem qualquer validação estatística.
- **Só suporta `content_type = news_article`**. Outro tipo de conteúdo levanta erro explícito (`UnsupportedContentTypeError`) em vez de cair silenciosamente na estratégia de parágrafo.
- **Não considera negação, ironia, citação de terceiros ou comparação** — conta termos positivos/negativos no parágrafo inteiro, sem entender contexto.
- **Identificação de entidade não é NER**: depende de uma lista curada manualmente em `config/entities.yaml` (match exato + fuzzy). Entidade fora dessa lista não é identificada.

Essa baseline existe para validar o pipeline ponta a ponta e servir de referência de comparação para uma técnica melhor (fase 2 planejada: LSTM com embeddings pré-treinados em português).

## Decisões em aberto / próximos passos

Ver [`context.md`, seção 9](context.md#9-decisões-em-aberto-específicas-deste-repositório) — inclui a técnica de classificação definitiva pós-baseline, correferência de entidade, dono das migrations do System DB quando o Analyser existir, e confirmação com o DevOps do nome da fila/exchange da notificação Classifier→Analyser.

## Estrutura de pastas

```
config/          Entidades, léxico e configurações versionadas (YAML)
db/migrations/   Migrations SQL do System DB, numeradas
docker/rabbitmq/ Topologia do RabbitMQ para desenvolvimento local (docker-compose)
docs/adr/        Decisões de arquitetura registradas
src/             Código do consumidor, publisher, pipeline de classificação e acesso a dados
tests/           Testes automatizados (pytest), incluindo setup do banco de teste
logs/            Log de execução (não versionado)
```
