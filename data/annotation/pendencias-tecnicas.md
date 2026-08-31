# Pendências técnicas do classifier

Consolidado em 2026-08-25 -- reorganizado pra separar o que já foi resolvido (registro histórico, resumido) do que ainda está em aberto (o backlog de verdade). Itens renumerados nessa reorganização; se algo antigo referenciar "item N", o número pode ter mudado.

## Correção de schema pelo DBA (2026-08-30) -- muda referências antigas neste documento

O Gustavo (DBA) corrigiu `entidade_matching.nr_partido` de `text` pra `integer` (tipo correto, batendo com `tabela_partido.nr_partido`/`tabela_candidatura.nr_partido`) e adicionou duas foreign keys: `nr_titulo_eleitoral_candidato -> tabela_politico` e `nr_partido -> tabela_partido`. Onde este documento (mais abaixo) disser "`entidade_matching.nr_partido` é `text`" ou tratar a tabela como sem FK, está desatualizado -- histórico de quando foi escrito, não descrição do estado atual.

**Efeito colateral real, encontrado em produção**: a query de `entities_store.load_active_entities_from_db` (`UNION ALL` entre políticos e partidos) tinha `NULL::text AS nr_partido` de um lado e `em.nr_partido` (agora `integer`) do outro -- quebrou com `psycopg.errors.DatatypeMismatch: UNION types text and integer cannot be matched` ao subir no ambiente do Anderson (Kubernetes). Corrigido com cast explícito (`em.nr_partido::text`), que funciona com os dois tipos -- 81/81 testes passando depois da correção. `scripts/sql/004_entidade_matching_schema.sql` também foi atualizado pra refletir o tipo `integer` e as duas FKs novas -- **`entidade_matching` não é mais uma tabela isolada**: replicar ela em prod agora exige que `tabela_politico` e `tabela_partido` já existam e estejam populadas antes, senão os `INSERT`s falham por violação de FK.

## Trabalho em andamento (2026-08-25, atualizado) -- se a sessão cair, retomar por aqui

- **Item 2**: ✅ RESOLVIDO -- colunas `pipeline_version`/`evidencia_match` + constraint confirmadas direto no banco (`app_db2`).
- **Item 3**: ✅ RESOLVIDO -- commit `0b0bb38`. Title Case exigido pra 7 partidos ambíguos, 4 testes novos, 37/37 passando.
- **Item 4**: ✅ MEDIDO -- commit `35fe33c`. Resultado preocupante: **127x mais lento** com 1.393 entidades (1.04ms -> 132ms por parágrafo), ~62% do tempo de processamento de uma mensagem só em matching de entidade no volume real. Virou item 7 (ver abaixo).
- **Item 1**: ⚠️ PARCIALMENTE RESOLVIDO, sem commit ainda (mudanças live no worktree principal, não isoladas). O que já funciona:
  - `entities_store.load_active_entities_from_db(conn)` -- carrega de `entidade_matching WHERE ativo = true`, testado contra `app_db2` real: 1393 entidades (1363 políticos + 30 partidos). `id` = `nr_titulo_eleitoral_candidato` (político) ou `nr_partido` (partido).
  - `main.py` migrado -- não chama mais `storage.run_migrations` em produção, carrega entidades do banco novo.
  - `entity_type` propagado ponta a ponta: `EntityMatch` (`entity_matcher.py`) e `ClassificationResult` (`pipeline.py`).
  - `classification_store.persist_classification_result_v2()` -- nova função pra gravar em `tabela_classificacao` (schema novo), com `ON CONFLICT ... DO NOTHING` pra idempotência. Testada com dry-run real contra `app_db2` (insert + rollback).
  - **✅ RESOLVIDO (2026-08-28)**: `message_handler.py` religado pra `persist_classification_result_v2` -- também corrigido um bug encontrado nessa função (INSERT usava a coluna `modelo_ia`, que não existe em `tabela_classificacao`; o nome real é `metodo_classificacao`, confirmado direto no banco). Checagem de idempotência prévia (`already_processed`) reescrita contra `tabela_classificacao` (`has_existing_classification`) -- ver limitação nova documentada na docstring dessa função (artigo sem NENHUMA entidade identificada nunca grava linha, logo é sempre reprocessado em redelivery; não duplica dado, só reprocessa à toa).
  - **✅ RESOLVIDO (2026-08-28), SUPERADO EM 2026-08-29**: notificação ao Analyser -- decisão da Bea (2026-08-28): publicação DIRETA via RabbitMQ (mesmo padrão Scraper->Classifier), não outbox em `app_db2` (ver ADR 0003, `docs/adr/0003-notificacao-analyser-publicacao-direta.md`). Falha de publicação era logada e não rejeitava a mensagem -- risco de perda silenciosa de notificação aceito e documentado.
    - **✅ SUPERADO (2026-08-29)**: a Bea criou manualmente `classifier_analyser_outbox` em `app_db2` (tabela de uso INTERNO do Classifier, ver `scripts/sql/003_classifier_analyser_outbox.sql`) e a notificação voltou a passar por um outbox transacional -- ver ADR 0004 (`docs/adr/0004-outbox-proprio-para-notificacao-analyser.md`). `src/message_handler.py` grava o payload em `classifier_analyser_outbox` na mesma transação de `tabela_classificacao`, antes do commit; `src/notify_analyser.py` (reativado, antes órfão) drena e publica de fato, com retry/circuit breaker. Os três módulos órfãos citados na ADR 0003 (`notify_analyser.py`, `notification_publisher.py`, `notifications_store.py`) foram ADAPTADOS para a chave nova (`id_artigo_scrape` + `pipeline_version`, no lugar de `run_id`) em vez de reescritos -- a lógica de claim/retry/circuit-breaker já era exatamente a desejada. **✅ RESOLVIDO (2026-08-29)**: agendamento do `src/notify_analyser.py` em produção -- Anderson (infra) confirmou que fica por conta dele, configurado manualmente no Gitea (CronJob em Kubernetes). Não é mais pendência do lado do Classifier/Bea.
    - **✅ RESOLVIDO (2026-08-29)**: formato do payload/contrato de dados -- decisão explícita da Bea: o modelo de dados do Classifier (`tabela_classificacao` + payload de `analyser_notifier.py`, ver `docs/dicionario-dados-analyser.md`) é a referência fixa, já entregue pra quem constrói o Analyser. Achado relevante durante a checagem: o protótipo atual do Analyser (README lido em 2026-08-29) não consome RabbitMQ/Postgres de jeito nenhum -- lê um arquivo JSON agrupado por ator (`{"ator": {...}, "itens": [...]}`), formato bem diferente do que o Classifier produz. **Decisão da Bea: quem constrói o Analyser adapta o consumo pro formato do Classifier, não o contrário** -- não é mais pendência deste lado. Só falta confirmar com o DevOps o nome real de fila/exchange (item de infra, não de formato).
  - **✅ RESOLVIDO (2026-08-28)**: `id_midia` -- lookup exato case-insensitive em `tabela_midia.nome_midia` a partir de `ScraperPayload.source_name` (`classification_store.resolve_id_midia`); sem correspondência, grava NULL e loga aviso, não bloqueia. Testado contra `app_db2` real (10 mídias cadastradas). **Medido com dado real em 2026-08-29**: comparando os 540 `source_name` reais já coletados pelo scraper contra o cadastro, só **33,5% batem** (181/540) -- a maioria das fontes reais (Veja, Valor Econômico, BBC etc.) simplesmente não está cadastrada em `tabela_midia` (só 10 veículos hoje), e parte do resto vem como domínio (`g1.globo.com`) em vez do nome cadastrado (`G1`). Não bloqueia nada (`id_midia` é nullable), mas registrado como limitação conhecida -- 2/3 das linhas de resultado ficam sem `id_midia` preenchido até alguém decidir investir em cadastrar mais veículos e/ou melhorar o lookup (aceitar domínio, abreviação).
  - pytest: 59/59 passando (2026-08-28) -- inclui `db/migrations/0005` (réplica de teste do schema novo: `tabela_classificacao`, `tabela_midia`, `tabela_politico`, `tabela_partido`), criada porque essas tabelas não fazem parte das migrations antigas do Classifier nem existem em `classifier_test` por padrão. `message_handler.py` também validado com dry-run real (INSERT + rollback) contra `app_db2`.
- **Item 7**: ✅ RESOLVIDO. Otimização (índice de aliases + pré-filtro antes do fuzzy + cache de regex próprio) trouxe **130,6ms -> 0,63ms por parágrafo (~208x)** com 1.393 entidades sintéticas, verificado com 83.200 comparações contra o resultado antigo (zero divergência) + 43 testes passando no worktree isolado. **Mesclado manualmente** em `src/entity_matcher.py` do worktree principal, junto com `entity_type` (item 1) e Title Case (item 3) -- os três conviviam em versões diferentes do arquivo. Teste novo copiado pra `tests/test_entity_matcher_prefilter.py`.
  - **Validação da mesclagem BLOQUEADA**: `pytest` não roda agora -- `100.64.0.1:5433` (host do `app_db2`/`classifier_test`) está inacessível (timeout de rede puro, nem chega a autenticar; confirmado com `nc -zv`). Não é erro da mesclagem, é a rede/VPN/servidor fora do ar agora. Assim que a conexão voltar, rodar `pytest -q` de novo antes de confiar no merge.
  - **✅ Validado com dado real (2026-08-29)**: rodado contra `entidade_matching` de verdade (não só sintético). Resultado: o ganho se sustenta, mas é ~2,5x menos eficaz que o número sintético sugeria.

    | Cenário | Entidades | ms/parágrafo |
    |---|---|---|
    | Real v1 (produção hoje) | 40 | **0,093 ms** |
    | Real completo (escopo cheio) | 1.393 | **1,773 ms** (vs. 0,63-0,71ms no sintético) |

    Causa raiz confirmada por medição (não especulação): vocabulário único de palavras no pré-filtro é 15x maior com nome real (2.083 palavras vs 138 do gerador sintético, que reusa um pool fechado de ~40 nomes) -- `process.extract` do rapidfuzz escala com o tamanho desse vocabulário. Mesmo assim, 1,773ms continua ordens de grandeza abaixo do baseline pré-otimização (130,6ms) -- conclusão "otimização necessária e funcional" continua de pé, só a margem é menor que a documentada antes. Hoje em produção (v1, 40 entidades) o custo é irrelevante (0,093ms, igual ao baseline de 10 entidades antigo). Só importa de novo quando uma fase futura ampliar o recorte de cargos.

## Mudança de escopo do projeto (2026-08-28) -- decidido em equipe

Classificação e raspagem vão ser feitas por fases, não tudo de uma vez. V1 do classifier: só candidatos a **presidente e vice-presidente** (aliases + partidos deles) -- não os ~1.400 da migração completa. Fases seguintes ainda não definidas (hipótese não confirmada: v2 governador+vice+senador, v3 deputado federal/estadual). Isso não invalida o trabalho do item 7 (otimização de performance) -- só passa a valer com prioridade menor pra v1 (poucas dezenas de entidades não sofrem o mesmo gargalo), mas continua necessário pras fases seguintes quando o volume voltar a crescer (avaliado pelo software-architect: manter como está, sem custo de manutenção real, não é over-engineering).

**Item 8: ✅ RESOLVIDO (2026-08-28)** -- confirmado direto no banco (consulta de leitura em `app_db2`) que `entidade_matching.ds_cargo` existe e está populado para `entity_type = 'politico'` (`'PRESIDENTE'`: 13 linhas, `'VICE-PRESIDENTE'`: 13 linhas, mais outros cargos fora de escopo). Confirmado também que **não dá pra saber o partido de um candidato só com `entidade_matching`** -- `nr_partido` só é preenchido em linhas de `entity_type = 'partido'`, nunca em linhas de político. O vínculo candidato -> partido vem de `tabela_candidatura` (mesmo schema compartilhado, `app_db2`), via `nr_titulo_eleitoral_candidato`, com cast explícito porque `tabela_candidatura.nr_partido`/`tabela_partido.nr_partido` são `integer` e `entidade_matching.nr_partido` é `text`.

Implementado em `entities_store.load_active_entities_from_db(conn, cargos)`: filtra políticos por `ds_cargo IN cargos` (JOIN com `tabela_candidatura`) e traz só os partidos cujo `nr_partido` aparece entre os partidos desses candidatos. Lista de cargos configurável em `config/settings.yaml` (`entity_scope.cargos`), lida em `src/main.py` -- não hardcoded, comentário explica onde mudar na v2. Validado contra dado real: 26 políticos + 14 partidos = 40 entidades, batendo com a contagem manual. Teste em `tests/test_entities_store.py` (leitura real contra `app_db2`, sem nenhum UPDATE/INSERT/DELETE).

**Confirmado: NÃO usar o campo `ativo`** pra recorte de fase -- filtro é só na query de leitura (`WHERE ativo = true` continua existindo, sem mudar seu significado; o corte por cargo é uma condição adicional, não uma reinterpretação do `ativo` compartilhado).

**Risco de produto ainda em aberto** (não resolvido por esta mudança, só registrado de novo): se o `scrapping-prisma` continuar raspando notícia geral sem filtrar por presidenciável, uma fração grande do conteúdo raspado não vai gerar classificação nenhuma -- alinhar com quem cuida da raspagem antes de considerar a v1 "fechada" ponta a ponta.

**Risco de produto/arquitetura, registrado por dois agentes independentes**: se o `scrapping-prisma` continuar raspando notícia geral (não filtrada por presidenciável) enquanto o classifier só processa esse escopo estreito, uma fração grande do que for raspado não vai gerar classificação nenhuma -- não é bug, é escopo, mas precisa estar alinhado com quem cuida da raspagem pra não parecer que "o sistema não está funcionando". Vale confirmar se os dois componentes vão derivar "presidenciável" do mesmo critério (`DS_CARGO`) ou de listas independentes (risco de desalinhar se uma candidatura mudar).

## Em aberto

### 1. Migrar `entity_matcher`/`main.py` de `config/entities.yaml` pra `entidade_matching` (app_db2)

**BLOQUEADO**: preciso de credenciais de acesso ao `app_db2` (ver "Trabalho em andamento" acima).

Decisão já tomada (2026-08-24): o YAML manual vai ser extinto, fonte de entidades passa a ser só a tabela `entidade_matching`. Falta implementar:

- `src/entities_store.py`/`src/main.py` pararem de ler `config/entities.yaml`, passarem a carregar de `entidade_matching`.
- Conferir que as 10 entidades hoje curadas à mão (Lula, Alckmin, Haddad, Tarcísio, Marina Silva, PT, PL, PSOL, União Brasil, Jair Bolsonaro) têm equivalente garantido na tabela nova antes do corte -- a maioria já tem (vieram do TSE como candidato real), mas não foi conferido um por um ainda.
- `Entity.id` precisa virar a chave real do TSE (`nr_titulo_eleitoral_candidato` pra político, `nr_partido` pra partido) em vez do slug artificial atual (`"lula"`, `"pt"`).
- `EntityMatch` (`src/entity_matcher.py`) e `ClassificationResult` (`src/pipeline.py`) precisam propagar um campo `entity_type` (`'politico'`/`'partido'`) -- hoje `entity.entity_type` existe mas é descartado no meio do caminho.
- `classification_store._insert_classifications` precisa de um branch: político preenche `nr_titulo_eleitoral_candidato` (deixa `nr_partido` NULL), partido o inverso.

### 2. `tabela_classificacao` (app_db2) sem idempotência nem auditoria de match

Faltam duas colunas: `pipeline_version` (+ constraint única com artigo/parágrafo/entidade, pra reprocessar mensagem não duplicar linha) e uma coluna de auditoria do match (`evidencia_match JSONB`, guardando tipo/score/alias). SQL já entregue pra Bea (`ALTER TABLE tabela_classificacao ADD COLUMN...`) -- **não confirmado se já rodou** no banco de verdade. Conferir antes de considerar resolvido.

### 3. Partidos sem forma segura de alias (sigla = nome completo)

7 partidos não têm uma forma "mais longa e segura" pra usar no lugar da sigla, porque a sigla JÁ É o nome inteiro: **AGIR, AVANTE, CIDADANIA, DEMOCRATA, PODEMOS, REPUBLICANOS, SOLIDARIEDADE** (+ PROGRESSISTAS/PP, risco menor, é sigla curta não palavra de dicionário). REPUBLICANOS é o mais grave -- partido do Tarcísio (53 candidatos no escopo B), e "os republicanos" também se refere ao partido americano em cobertura internacional que já aparece no corpus.

**Solução validada contra dado real** (não é achismo -- testado em `data/raw/articles_202608191040.csv`, 229 ocorrências reais de "Republicanos"): esses partidos aparecem sempre em **Title Case** no texto ("Republicanos"), nunca em CAIXA ALTA. Then exigir Title Case exato (não case-insensitive) no match resolve Republicanos/Avante/Cidadania/Podemos/Solidariedade quase por completo, e reduz (não elimina) o risco de Democrata/Agir.

Implementação pendente: `_exact_match` (`src/entity_matcher.py`) precisa de uma flag por alias pra ligar/desligar `re.IGNORECASE` -- hoje é fixo pra todo alias. Código real, não SQL.

### 4. Performance do `entity_matcher` em ~1.400 entidades

Nunca medido -- todo teste até agora foi com as 10 entidades do YAML. `entidade_matching` já tem 1.363 políticos + 30 partidos (escopo B). Antes de ligar isso em produção, medir tempo de processamento por parágrafo com o volume real.

### 5. Suspeita (não confirmada) de entidade não reconhecida em algum parágrafo

Se aparecer um caso onde uma entidade mencionada no texto comprovadamente não gera NENHUMA linha em lugar nenhum (nem no manifest completo, não só no arquivo de um revisor), registrar aqui com o `paragraph_id` exato. Nenhum caso confirmado até agora -- os que pareciam isso eram round-robin (ver resolvidos).

### 6. Desempate de aliases da mesma entidade depende da ordem do YAML (baixa prioridade)

Quando dois aliases da MESMA entidade empatam no score fuzzy, `match_entities` mantém o primeiro da lista (`fuzzy_score > best.score` é estritamente maior, não `>=`) -- quem "ganha" depende da ordem no YAML/tabela, não de especificidade. Confirmado que acontece (49 ocorrências reais, causadas por mojibake tipo `TarcÃsio` vs `Tarcísio`). Impacto só no campo de auditoria (`entity_matched_alias`), não na classificação em si -- baixa prioridade, mas some junto com o YAML se o item 1 for resolvido primeiro (a chave passa a ser TSE, não mais slug com múltiplos aliases artesanais).

## Resolvido

- **Regras de anotação** (autocitação, julgamento explícito, partido entre parênteses, apuração sem julgamento, reação de plateia, comparação metafórica) -- incorporadas em `docs/protocolo-anotacao.md`.
- **Alias "Bolsonaro" sozinho** em `jair-bolsonaro` (casava com Flávio/Michelle/Carlos) -- removido do `config/entities.yaml`. "Tarcísio"/"Tarcisio" soltos e "PT de Lula" também removidos por precaução.
- **PSB ausente da lista curada** -- superado pela migração pra `entidade_matching` (PSB já vem do TSE como partido real).
- **"Só reconhece 1 entidade"** -- não era bug, era o round-robin do `build_annotation_batches.py` dividindo entidades de um mesmo parágrafo entre revisores diferentes.
- **Fuzzy match de string inteira** -- `_fuzzy_match_score` comparava a janela inteira concatenada em vez de palavra por palavra, causando falso positivo em "União Brasil"/"no Brasil" e "presidente Lula"/"presidente da". Corrigido comparando palavra por palavra com o mínimo dos scores. Testes de regressão em `tests/test_entity_matcher.py`.
- **Duplicata de partido em `entidade_matching`** -- script de inserção rodado duas vezes sem `DELETE` antes, causando cada partido aparecer 2x. Corrigido com `DELETE` mantendo o `id` mais baixo.
- **53 aliases de político com colisão de nome comprovada por dado real** -- palavra aparecendo em 2+ candidatos reais diferentes entre os 20.724 do TSE (ex.: FERREIRA 703x, RENAN em duas pessoas diferentes, REGINALDO em duas pessoas diferentes). Removidos via `UPDATE`, mantendo o nome completo de cada um.
- **Duplicata dentro do próprio array de aliases** (ex.: `["REPUBLICANOS", "REPUBLICANOS"]`, quando sigla = nome do partido) -- corrigido com `jsonb_agg(DISTINCT elem)`.
