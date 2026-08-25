# Pendências técnicas do classifier

Consolidado em 2026-08-25 -- reorganizado pra separar o que já foi resolvido (registro histórico, resumido) do que ainda está em aberto (o backlog de verdade). Itens renumerados nessa reorganização; se algo antigo referenciar "item N", o número pode ter mudado.

## Trabalho em andamento (2026-08-25) -- se a sessão cair, retomar por aqui

Rodando em paralelo agora:
- **Item 3** (case-sensitivity nos aliases de partido) -- agente `backend-engineer` disparado, implementando em `src/entity_matcher.py` + testes em `tests/test_entity_matcher.py`. Se der erro/travar no meio, conferir se esses arquivos já têm alguma mudança parcial antes de disparar de novo.
- **Item 4** (benchmark de performance) -- agente `backend-engineer` disparado, criando `scripts/benchmark_entity_matcher.py` com dado sintético (~1.400 entidades) + parágrafos reais de `data/raw/articles_202608191040.csv`. Vai reportar números reais de tempo/parágrafo, ainda não sei o resultado.
- **Item 2**: script pronto em `scripts/sql/002_tabela_classificacao_idempotencia_auditoria.sql` -- **falta a Bea rodar contra o banco de verdade** (dados fictícios, script já usa `TRUNCATE` antes, sem backfill).
- **Item 1**: **bloqueado** -- preciso de credenciais do `app_db2` no `.env` (`APP_DB2_HOST`/`PORT`/`DB`/`USER`/`PASSWORD` ou similar) pra escrever E testar a migração de verdade, não só escrever código no escuro. Pedido feito à Bea, aguardando.

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
