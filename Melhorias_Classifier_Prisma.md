# Melhorias do Classificador (classifier-prisma)

*Erros atuais, como resolver e plano de execução*

> Levantamento de 29/09/2026. Base:
> - o diagnóstico `Diagnostico_panoramas_Prisma.md` (causas C1–C5);
> - o código do `classifier-prisma` na develop (`4e273b9`), lido por completo;
> - os consumidores (`analyser-prisma`, `api-prisma`) e o `scrapping-prisma`;
> - medições feitas contra o conjunto anotado (`data/annotation/consolidated.csv`);
> - um piloto com LLM local e duas pesquisas bibliográficas.
>
> Os materiais de apoio (scripts do piloto, relatório de pesquisa com links) estão listados na seção 9.

---

## 1. Resumo

1. **O léxico não serve para decidir as abas.** Contra o conjunto anotado, ele acerta 13% do que marca como positivo e 44% do que marca como negativo, e encontra só 11% dos positivos e 21% dos negativos reais. O macro-F1 é 0,41.
2. **O rótulo por parágrafo está errado por construção.** Em 35% dos parágrafos com mais de uma entidade (64 de 183), os anotadores deram rótulos diferentes para cada entidade. O classificador dá o mesmo rótulo a todas.
3. **Regra + modelo corretor não resolve.** Um corretor que só revisa o que a regra marcou pode desfazer falsos positivos, mas nunca recupera o que a regra deixou passar como neutro. No piloto, o macro-F1 subiu só de 0,35 para 0,38 e o recall ficou em 10 a 14%. O corretor que revisa todos os pares vendo o rótulo da regra fez 0,49, abaixo do modelo sozinho (0,53), com o mesmo custo. A literatura mostra ainda que mostrar o rótulo anterior ao LLM o ancora: ele deixa de corrigir quase metade dos erros.
4. **Recomendação: o modelo classifica e a regra sai da decisão.**
   - Agora: um LLM local classifica cada par (parágrafo, entidade) sem ver o rótulo da regra.
   - Depois: o LLM pré-rotula, humanos revisam e fine-tunamos um encoder em português.
   - Em produção: cascata. O encoder classifica tudo e o LLM entra só nos casos de baixa confiança.
   - A regra fica como fallback e como linha de base na avaliação.
5. **Condição para trocar em produção.** O modelo pronto (llama3.1:8b, sem exemplos) acha muito mais (recall de 76% nos negativos), mas erra por excesso: só ~31% do que ele põe nas abas está certo. Antes de trocar, precisamos de prompt com exemplos, um modelo melhor, validação e **modo sombra** (seção 4.4).
6. **Contexto para o Analyser: sim. Contexto para o classificador: com cuidado.**
   - O classificador precisa guardar título, linha fina, a ordem dos parágrafos e os parágrafos sem entidade, para o Analyser montar o contexto (C4).
   - No piloto, dar título e vizinhos ao modelo 8B **piorou** a classificação (macro-F1 0,53 → 0,45), porque ele passou a julgar a matéria inteira. O contexto só entra no classificador quando o harness mostrar que ajuda.
7. **Há problemas de infraestrutura que bloqueiam qualquer troca de método.** A versão de classificação não é identificada, o Analyser rejeita campos novos, a API conta todas as versões, e uma queda do LLM mandaria a fila inteira para a DLQ. Isso vem antes do modelo.

---

## 2. Erros atuais

### 2.1 Do diagnóstico (C1–C5)

| Código | Erro | Onde no código | Efeito (casos do diagnóstico) |
|---|---|---|---|
| **C1** | O rótulo é do parágrafo, não da entidade. Conta 27 palavras positivas e 31 negativas e copia o resultado para todos os citados. Uma única palavra já dá confiança 1,00. Não trata negação, plural, ironia nem quem fala sobre quem. | `src/lexicon.py:62-95`, `src/pipeline.py:58-76`, `config/lexicon.yaml` | "Coligação de Lula **acusa** Flávio" vira negativo para Lula. "TRE **rejeitou** pedido de Tarcísio contra Haddad" vira negativo para Haddad/PT. "O **avanço** do Cury" vira positivo para Zema (casos 4 a 7). |
| **C2** | Sigla reconhecida sem contexto. | `src/entity_matcher.py:42-50, 95-112` | "Washington, DC" vira o partido DC (21 parágrafos). |
| **C3** | Palavra do léxico dentro de nome próprio. | `src/lexicon.py:79-80` | A candidata "**Vitória** Leite" vira elogio ao DC. |
| **C4** | O título e o texto completo não são guardados, e a notificação ao Analyser não leva texto nem título. | `src/classification_store.py:165-179`, `src/analyser_notifier.py:65-83` | O Analyser não sabe qual é a escola (Missão) nem de que julgamento se trata (Girão). |
| **C5** | Cada quebra de linha vira um parágrafo, então itens de lista e linhas de tabela ficam soltos. | `src/segmentation.py:16, 40-52` | "- Eduardo Girão (Novo) – 1%" e "\| VITÓRIA LEITE \| DC \| ... \|" são classificados sem a frase que os explica. |

### 2.2 Achados novos nesta revisão

| # | Erro | Onde | Efeito |
|---|---|---|---|
| **N1** | **A versão da classificação não é identificada.** A chave de idempotência (`uq_tabela_classificacao_idempotencia`) e a checagem `has_existing_classification` usam o `pipeline_version` **do scraper** (`'v1'`), não o método do classificador. O outbox tem `UNIQUE (id_artigo_scrape, pipeline_version)`. | `classification_store.py:77-104, 178`, `message_handler.py:77-86`, `scripts/sql/003…:78` | Um método novo com o mesmo `pipeline_version` é descartado em silêncio (`ON CONFLICT DO NOTHING`). Se o scraper republicar com outra versão, a API conta em dobro, porque `ClassificacaoRepository.contarPorSentimento` não filtra versão. Não há como rodar dois métodos lado a lado. |
| **N2** | **O índice do parágrafo não é gravado.** Vai só no payload da notificação. | `classification_store.py:167-171` | O Analyser não consegue buscar os vizinhos e faz um casamento posicional frágil (`enriquecimento.py:254-265`). |
| **N3** | **Parágrafo repetido dentro do mesmo artigo some.** A chave usa o texto, não o índice. | constraint de idempotência | Acontece em 15 dos 472 artigos da amostra. O payload lista os dois índices, o Analyser falha no casamento e grava `id_classificacao` nulo. |
| **N4** | **Não existe o rótulo "não relacionado".** | schema de `tabela_classificacao` | 17,6% das unidades anotadas são menções incidentais ou erros de identificação, e hoje entram como neutro na barra. Parte disso vem do matcher antigo usado na anotação (104 dos 226 casos são o alias "Bolsonaro" sozinho, que já foi removido): **medir de novo em produção.** |
| **N5** | **Mais siglas ambíguas.** "UP" (Unidade Popular) casa com "Go Up Entertainment" (28 ocorrências no corpus); "DEMOCRATA" casa com o partido americano; "PL" pode casar com "PL 2630". | `entity_matcher.py` | Falsos positivos de entidade, como no caso DC. |
| **N6** | **Recall perdido por alias.** Novo e Missão só têm os aliases `PARTIDO NOVO`/`PARTIDO MISSÃO`, então "Romeu Zema (Novo)" não gera nenhum match. O PSTU está cadastrado com espaço duplo e só casa pelo fuzzy. | `entidade_matching` | Menções reais aos partidos ficam de fora. |
| **N7** | **Uma queda do LLM derrubaria a fila.** Toda exceção inesperada vira `nack(requeue=False)`, ou seja, DLQ. A conexão pika não tem heartbeat, e a transação fica aberta durante todo o processamento. | `rabbitmq_consumer.py:96-112`, `message_handler.py:77` | Com um LLM no pipeline: fila inteira na DLQ numa queda do Ollama, conexão derrubada em chamadas longas e "idle in transaction" por minutos no `app_db2` compartilhado. |
| **N8** | **O contrato com o Analyser é rígido.** `NotificacaoClassifier` usa `extra="forbid"`, `schema_version: Literal[1]` e três rótulos. | `analyser-prisma/analyser/schemas.py:32-50` | Qualquer campo novo, `nao_relacionado` ou `schema_version: 2` quebra o consumo. **O Analyser precisa mudar antes.** |
| **N9** | **Não há testes no CI.** `.gitea/workflows/ci-cd.yaml` só faz build e deploy. | CI | Nenhuma regressão de classificação é detectada. |
| **N10** | **Documentação divergente do código.** O dicionário diz `"tipo": "exato"\|"aproximado"`, mas o código grava `exact`/`fuzzy`. O exemplo de `pipeline_version` (`lexicon-baseline-v1`) está errado: o valor real é `'v1'`. | `docs/dicionario-dados-analyser.md` | Confunde quem consome a tabela. |
| **N11** | **O Ollama do Analyser não aguenta um segundo cliente.** Roda com `OLLAMA_NUM_PARALLEL=1` e `OLLAMA_MAX_LOADED_MODELS=1`. | `analyser-prisma/compose.yml:53-54` | Um classificador usando outro modelo forçaria troca de modelo a cada chamada. |

---

## 3. Regra + corretor ou modelo? (a pesquisa)

### 3.1 O que medimos

**Léxico atual contra o conjunto anotado** (1.054 unidades com rótulo positivo, negativo ou neutro):

| Classe | Precisão | Recall | F1 |
|---|---|---|---|
| negativo | 0,44 | 0,21 | 0,28 |
| neutro | 0,80 | 0,88 | 0,84 |
| positivo | 0,13 | 0,11 | 0,12 |
| **macro-F1** | | | **0,41** |

**Piloto** (split de teste, 170 unidades, `llama3.1:8b` local, temperatura 0, saída JSON com papel da entidade → rótulo). Precisão da aba = das unidades que a estratégia põe na aba, quantas o anotador também pôs.

| Estratégia | Macro-F1 (3 classes) | Negativo P / R | Positivo P / R | Neutro F1 | Precisão aba NEG (itens) | Precisão aba POS (itens) | s/chamada |
|---|---|---|---|---|---|---|---|
| **L** — léxico atual | 0,35 | 0,25 / 0,10 | 0,10 / 0,07 | 0,82 | 0,20 (10) | 0,10 (10) | — |
| **C-hits** — regra + LLM corretor (revisa o que a regra marcou) | 0,38 | 0,30 / 0,14 | 0,25 / 0,07 | 0,84 | 0,25 (12) | 0,25 (4) | 3,1 |
| **C-all** — LLM corretor revisa todos os pares vendo o rótulo da regra | 0,49 | 0,33 / 0,81 | 0,26 / 0,64 | 0,62 | 0,28 (61) | 0,26 (34) | 5,7 |
| **M-noctx** — LLM classifica o par, sem contexto | **0,53** | 0,33 / **0,76** | 0,32 / **0,64** | 0,69 | 0,31 (52) | 0,28 (32) | 4,5 |
| **M-ctx** — LLM com título + parágrafo anterior e posterior | 0,45 | 0,32 / 0,95 | 0,23 / 0,71 | 0,51 | 0,28 (71) | 0,22 (45) | 5,3 |

Leitura:

- **O corretor tem teto de recall, como previsto.** Ele só vê 20 das 170 unidades. Os 18 negativos que a regra chamou de neutro nunca chegam até ele.
- **O corretor que revisa tudo custa o mesmo que o modelo sozinho e rende menos** (0,49 contra 0,53). É a ancoragem da literatura aparecendo nos nossos dados.
- **O modelo classificando tudo é a única estratégia que encontra as críticas e os elogios reais.** Mas, com o modelo pronto e sem exemplos, ele marca sentimento demais: 30 dos 112 neutros viraram negativo e 19 viraram positivo.
- **O 8B ignora as regras do protocolo.** Em ~85% das respostas ele diz que a entidade é o alvo da avaliação. Erra autocitação ("Lula se apresentou como um peão" → positivo), fato sem julgamento, partido entre parênteses e quem critica × quem é criticado. Quase nunca usa `nao_relacionado` (1 acerto em 23). Nos parágrafos em que os anotadores divergiram por entidade, nenhuma estratégia passou de 13 acertos em 30. **O ganho da classificação por entidade depende de um modelo que siga essas regras**: few-shot e qwen3.5:9b são o próximo teste (Fase 0.5).
- **O contexto piorou o 8B.** Com a matéria à vista, ele rotula o parágrafo pelo tom geral dela: o neutro caiu de 0,69 para 0,51.
- **Limitações:**
  - a amostra é pequena (21 negativos e 14 positivos no teste), então as diferenças têm margem larga;
  - a concordância entre anotadores é moderada (kappa 0,42), o que limita qualquer modelo;
  - o llama3.1:8b não é o modelo de produção (qwen3.5:9b);
  - o prompt é zero-shot, sem exemplos;
  - os anotadores viram só o parágrafo, então parte da "piora" com contexto pode ser o modelo usando informação que eles não tinham.

### 3.2 O que diz a literatura

- **Léxicos falham em sentimento por alvo em política no português.** SentiLex, OpLexicon e LIWC ficaram entre 2 e 39 de macro-F1, contra 57,9 do ChatGPT (Seno et al., PROPOR 2024).
- **Mostrar o rótulo anterior ancora o LLM.** Ele deixa de fazer 48% das correções e ainda inverte 10% dos acertos; nem pedir raciocínio nem avisar para ignorar resolve (Kapetanovic et al., CIKM 2026). Autocorreção sem sinal externo não funciona (Huang et al., ICLR 2024).
- **Para notícias com sentimento por entidade**, o análogo mais próximo é o RuSentNE-2023 (72% neutro, kappa 0,5):
  - encoders fine-tunados fizeram 66,7 de F1;
  - ChatGPT zero-shot, 60,1;
  - modelos de 7B zero-shot, entre 45 e 50.
- **Encoders fine-tunados passam de LLMs grandes zero-shot a partir de 200 a 500 exemplos** (Bucher & Martini 2024).
- **Cascata** em notícias: encoder sozinho 73, LLM local sozinho 42,5, encoder que repassa ao LLM os casos incertos 77,3 (Chatterjee et al. 2026).
- **O plano antigo de "LSTM + embeddings NILC" está superado.** Métodos pré-BERT ficam cerca de 10 pontos abaixo (NewsMTSC).
- **Meta realista com kappa 0,42: macro-F1 entre 0,55 e 0,65.** Acima de 0,70 é improvável contra o rótulo majoritário.

### 3.3 Decisão

| Opção | Qualidade | Custo | Veredito |
|---|---|---|---|
| Manter só a regra | Muito baixa (0,35–0,41) | ~0 | ❌ |
| Regra + LLM corretor só nos casos marcados | Baixa: o recall não sobe (0,38) | Baixo (~12% dos pares vão ao LLM) | ❌ Teto de recall |
| Regra + LLM corretor em todos os pares, vendo o rótulo da regra | Pior que o LLM sozinho: 0,49 contra 0,53 (ancoragem) | Igual ao LLM sozinho | ❌ Custa o mesmo e é pior |
| **LLM classifica cada par** | Média hoje (0,53); sobe com exemplos e modelo melhor | ~4–5 s por parágrafo local (GPU bem menos) | ✅ **Passo 1** |
| **Encoder fine-tunado + LLM só na incerteza** | A melhor evidência publicada | Barato (~20–60 ms por par em CPU) | ✅ **Destino (passo 2)** |

**A regra continua em três papéis:**
1. **Fallback** marcado (`metodo=…+fallback`), por entidade, quando a saída do LLM for inválida.
2. **Linha de base** no harness de avaliação.
3. **Filtro barato de ruído** nas linhas de tabela e blocos repetidos (seção 5).

A regra deixa de decidir a aba.

### 3.4 Jev e Laya (pesquisados a pedido)

- **Jev** (TypeSafe AI, setembro de 2026) é um modelo proprietário, só por API, para decisões tipadas.
  - Não vale como peça principal: é externo, está em early access, não pode ser retreinado e o foco é inglês.
  - Num benchmark de 15 tarefas sutis, ficou em mediana 11,6 pontos de macro-F1 abaixo do melhor LLM.
  - É muito barato (~US$2 por mês no nosso volume), então serve no máximo de comparação pontual, se toparmos mandar texto a uma API externa.
- **Laya** (Convai, Apache 2.0) é o clone aberto. O `laya-multilingual` (mmBERT-base, 322M) cobre português.
  - Sem fine-tune é quase acaso, então não substitui o LLM.
  - "Laya retrained" não é um checkpoint: vem de um post que compara o Laya fine-tunado com o pronto, em inglês e com dados sintéticos.
  - **Entra como mais um candidato a encoder no passo 2**, treinado no mesmo split que o BERTimbau e o Albertina, e só é adotado se ganhar ou empatar. A saída de confiança por opção é um gatilho natural para a cascata.
  - Contra: o projeto tem cerca de 2 semanas, e não há evidência em português nem em sentimento por alvo.

---

## 4. Como resolver

### 4.1 C1 — classificar por entidade com modelo

**Interface plugável.** Arquivos novos:
- `src/classifiers/base.py`
- `lexicon_classifier.py`
- `ollama_classifier.py`
- `validation.py`

Tipos:

```python
Rotulo = Literal["positivo", "negativo", "neutro", "nao_relacionado"]
Papel  = Literal["avaliada", "avaliadora", "vitima", "mencionada", "lista"]

class ClassificadorEntidade(Protocol):
    metodo: str
    def classificar(self, janela: Janela, entidades: Sequence[EntidadeAlvo]) -> list[ClassificacaoEntidade]: ...
# ClassificacaoEntidade: entity_id, rotulo, papel, evidencia (trecho literal), confianca, metodo, versao_prompt, fallback
```

**Chamada ao modelo:**
- Uma chamada por parágrafo, com todas as entidades casadas nele (1,5 entidade por parágrafo em média).
- A saída pede primeiro o **papel da entidade** (quem avalia, quem é avaliado, vítima, mencionada) e depois o rótulo. Isso ataca diretamente "quem acusa não é acusado".
- As regras vêm de `docs/protocolo-anotacao.md` (casos difíceis 1 a 11).
- **O modelo nunca vê o rótulo do léxico.**

**Validação da saída:**
- os `entity_id` devolvidos são exatamente os enviados;
- rótulo e papel estão dentro dos valores previstos;
- a evidência é um trecho que existe no texto.

Se falhar, faz um retry com o erro. Se falhar de novo, usa o léxico só naquela entidade, com `fallback=true`.

**Prompt:**
- versionado em `prompts/classificacao_entidade/vN.txt`, com hash verificado na subida;
- `metodo = llm-<modelo>-p<N>`;
- os exemplos (few-shot) vêm só do split `train`.

**Melhorias a testar antes de ligar, todas no harness (4.5):**
1. few-shot com os casos difíceis (acusador × acusado, vítima, autocitação, partido entre parênteses);
2. **neutro como padrão explícito**: só marcar positivo ou negativo quando houver julgamento escrito sobre a entidade (regra 3 do protocolo);
3. qwen3.5:9b, a família de produção, com `think:false`;
4. votação de 3 amostras (self-consistency) para gerar confiança e decidir quando repassar;
5. contexto só como "referência para entender quem é quem", com a instrução de classificar só o parágrafo-alvo. Medir de novo e só manter se não piorar.

**Queda do modelo:**
- nova exceção `DependenciaIndisponivel` → `nack(requeue=True)`, pausa com backoff (60 s a 15 min) e checagem de `/api/tags` antes de voltar;
- **nunca** cair para o léxico em massa, porque isso misturaria métodos no mesmo período;
- processar em thread, com heartbeat no pika;
- fazer a chamada ao modelo **fora** da transação do banco (N7).

**Infra.** Escolher entre usar o mesmo Ollama e o mesmo modelo do Analyser (sem troca de modelo, mas disputando a fila) ou uma instância dedicada. Decidir medindo a vazão contra o volume real (~9 parágrafos com entidade por matéria).

### 4.2 C4 — contexto para o Analyser (título, linha fina, parágrafo anterior e posterior)

Recomendação: guardar a matéria segmentada no `app_db2`, uma linha por parágrafo, e gravar o índice na classificação.

```sql
CREATE TABLE tabela_artigo (
  id_artigo_scrape BIGINT PRIMARY KEY,
  titulo TEXT NOT NULL,
  linha_fina TEXT,                 -- NULL até o scraper enviar
  link_noticia TEXT NOT NULL,
  dominio TEXT NOT NULL,
  id_midia INTEGER REFERENCES tabela_midia(id_midia),
  data_publicacao_noticia TIMESTAMP,
  hash_texto CHAR(64) NOT NULL,
  registrado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tabela_paragrafo (
  id_artigo_scrape BIGINT NOT NULL REFERENCES tabela_artigo,
  versao_segmentacao TEXT NOT NULL,          -- 'seg-v2'
  indice INTEGER NOT NULL,
  texto TEXT NOT NULL,
  tipo_bloco TEXT NOT NULL,                  -- paragrafo | item_lista | linha_tabela | intertitulo
  indice_introducao INTEGER,                 -- frase que introduz a lista/tabela (C5)
  hash_normalizado CHAR(64) NOT NULL,        -- blocos repetidos (R1)
  dominio TEXT NOT NULL,
  bloco_repetido BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (id_artigo_scrape, versao_segmentacao, indice)
);

ALTER TABLE tabela_classificacao ADD COLUMN indice_paragrafo INTEGER;
```

- **Vizinhos:** o Analyser busca com `WHERE indice BETWEEN i-1 AND i+1`, **incluindo parágrafos que não citam ninguém**. Isso resolve os casos Missão e Girão (A4 no diagnóstico).
- **Título:** gravado aqui e enviado também na notificação.
- **"Manchete" / linha fina:** o scraper **ainda não extrai**. Mas ele já chama `trafilatura.bare_extraction(..., with_metadata=True)` (`scrapping-prisma/src/article_extractor.py:131`), que devolve `description`. Falta só persistir esse campo e mandá-lo no payload. É uma mudança pequena **no scraper**. O classificador lê `description` quando vier; o parser já ignora campos extras, então não precisa de `schema_version` novo.
- **Notificação v2:**
  - leva `titulo`, `id_execucao`, `metodo_classificacao` e `id_classificacao` por item, o que acaba com o casamento posicional;
  - não leva texto;
  - não inclui itens `nao_relacionado`;
  - fica atrás da flag `ANALYSER_NOTIFICATION_SCHEMA_VERSION`, com 1 como padrão até o Analyser aceitar a v2 (N8).
- **Alternativas descartadas:**
  - colunas `paragrafo_anterior`/`posterior` em cada linha: triplica o texto, fixa a janela e não resolve o título;
  - tudo no payload: o banco deixa de ser a fonte da verdade e o catch-up pelo Postgres perde o contexto.
- **Histórico:** o classificador não lê o banco do scraper (ADR 0001). O scraper cria `analysis_jobs` com `pipeline_version='v2'` e republica em lotes (`--limit`). O classificador então segmenta e grava artigo e parágrafos.

### 4.3 N1 — versão da classificação, versão vigente e reprocessamento

- Criar a tabela `tabela_execucao_classificacao`, com uma linha por (artigo, `pipeline_version`, `versao_segmentacao`, `metodo_classificacao`) e a coluna `vigente`. Um índice único parcial garante **uma execução vigente por artigo**.
- `tabela_classificacao` ganha `id_execucao`, `papel_entidade` e `detalhe_classificacao` (jsonb com evidência, versão do prompt, latência, fallback e bloco repetido).
- A nova chave única passa a ser `(id_execucao, indice_paragrafo, entidade)`, o que também resolve N3.
- A view `vw_classificacao_vigente` filtra a execução vigente, os três rótulos e os blocos que não são repetidos. **A API e o Analyser passam a ler a view**, o que resolve a contagem em dobro.
- Migração do legado: uma execução `origem='legado'` vigente por artigo.
- A antiga `uq_tabela_classificacao_idempotencia` só é removida (DDL parte B) depois que a versão nova estiver no ar.
- `scripts/reclassify.py`:
  - filtros por método, período, domínio, limite e taxa;
  - modo `--sombra` ou `--promover`;
  - uma transação por artigo, e retomar é seguro;
  - na promoção grava o evento `promovido` no outbox, para o Analyser invalidar os resumos da execução antiga.

### 4.4 N4 — "não relacionado" e modo sombra

- Gravar `nao_relacionado` em `classificacao_sentimento`, com um `CHECK` sobre os quatro valores. A view e o payload v2 excluem esse rótulo, então a barra passa a contar só menções reais. **Avisar o time da página que a base da barra muda.**
- **Modo sombra:**
  - `settings.classifier.metodo_vigente: lexicon`, `metodos_sombra: [llm-…]`;
  - o consumidor grava as duas execuções, e a sombra fica com `vigente=false` e sem notificação;
  - 1 a 2 semanas em sombra, com amostragem humana semanal, antes de promover.

### 4.5 Avaliação contínua (pré-requisito da troca)

- **Harness** (`src/evaluation/`, `scripts/evaluate_classifier.py`):
  - roda qualquer classificador sobre `consolidated.csv`, reconstruindo título e vizinhos a partir de `data/raw`;
  - métricas: P/R/F1 por classe, macro-F1 com 3 e 4 classes, **precisão das abas** por artigo × entidade (emulando `separar_por_eixo` do Analyser), taxa de JSON inválido e de fallback, latência;
  - intervalo de confiança por bootstrap reamostrando **artigos**;
  - `validation` para iterar; `test` uma única vez por candidato.
- **CI** (N9):
  - job `pytest` antes do build;
  - job `eval-offline` com léxico e matcher, que falha se a métrica cair em relação a `eval/baselines/`;
  - o LLM não roda no CI. O PR que troca o método precisa anexar o relatório, e o CI confere modelo, hash do prompt e limites.
- **Critério para promover o LLM (proposta):**
  - precisão das abas NEG e POS **≥ 0,60**, com intervalo de confiança acima do léxico;
  - macro-F1 (3 classes) ≥ 0,55;
  - precisão de `nao_relacionado` ≥ 0,70 (senão, o sistema esconde menções reais);
  - fallback < 2%;
  - vazão suficiente para o volume diário.
- **Dados:**
  - fechar as 611 unidades pendentes de resposta e as 114 pendentes de revisão;
  - na rodada 2 de anotação, mostrar título e vizinhos aos anotadores e atualizar o protocolo;
  - produção semanal amostrada (`scripts/sample_production.py`).

### 4.6 C2, N5, N6 — siglas e aliases

- **Siglas curtas** (até 4 letras, todas em maiúscula no cadastro): exigir **caixa alta exata**. Só isso já resolve "Go Up".
- **Siglas marcadas como ambíguas** (DC, UP, PL, definidas por medição): exigir contexto partidário. Serve qualquer um destes:
  - a sigla entre parênteses, com UF opcional: `(PL-RJ)`;
  - uma palavra de contexto até ±6 palavras: partido, legenda, sigla, filiado, coligação, candidato, deputado;
  - uma enumeração junto com outra sigla já casada ("PT, PL e DC").
- **Exclusões por alias**, lidas de `config/entity_rules.yaml`:
  - `Washington,? D.?C.?`;
  - `PL nº? <número>`;
  - "Democrata" em frase com EUA, americano, Biden, Harris ou Trump.
- O PT **não** entra na lista de ambíguas, porque "o PT decidiu…" perderia recall.
- **Recall:** aceitar `Novo`, `Missão` e `Avante` quando aparecem entre parênteses logo depois de um nome ("Zema (Novo)").
- Normalizar espaços nos aliases (`" ".join(a.split())`) e abrir ticket ao DBA para corrigir o PSTU.
- Tirar `_CASE_SENSITIVE_ALIASES` do código e levar para o YAML.
- Testes com todos os casos reais citados.

### 4.7 C3 — léxico enquanto existir

Antes de contar as palavras, mascarar:
- os trechos dos aliases casados;
- sequências de 2 ou mais palavras capitalizadas fora do início da frase ("Vitória Leite");
- linhas de tabela, que passam a valer neutro.

Também subir `model.version`. Isso vale **já**, porque o léxico continua como método vigente até a promoção do LLM.

### 4.8 C5 e R1 — segmentação e blocos repetidos

- **`seg-v2`:** o segmentador passa a devolver blocos com tipo.

  | Tipo | Critério |
  |---|---|
  | `linha_tabela` | 2 ou mais `\|` |
  | `item_lista` | começa com `-`, `•`, `*`, `–` ou `1.` |
  | `intertitulo` | linha curta sem pontuação final |
  | `paragrafo` | o resto |

  - Os itens consecutivos apontam para a frase que os introduz (`indice_introducao`), que entra sempre na janela do classificador e fica visível para o Analyser.
  - Os itens continuam como unidades próprias: fundir tudo cria unidades enormes (uma tabela de 30 candidatos).
  - Na amostra, há 393 linhas de lista em 8.046 parágrafos.
- **Blocos repetidos** (mitigação no classificador; a correção de verdade é no scraper):
  - `hash_normalizado` do texto sem "há N horas", datas e nome de agência;
  - `bloco_repetido` quando o mesmo hash aparece em 3 ou mais artigos do mesmo domínio em 30 dias;
  - um job diário marca as primeiras ocorrências de forma retroativa.
  - **Na primeira fase só marca, não exclui.** A amostra tem repetições legítimas, como o contexto histórico que um mesmo veículo repete.

---

## 5. Plano de execução

O caminho crítico é: **medir → schema + leitores → gravar execuções → sombra → promover**. As correções do léxico e do matcher andam em paralelo e dão ganho imediato.

### Fase 0 — Medir (1 a 2 semanas; pré-requisito das decisões)

| # | Entrega | Depende de | Paralelo? |
|---|---|---|---|
| 0.1 | Job de testes no CI (N9) | — | ✅ |
| 0.2 | Harness de avaliação + baseline `lexicon-v1` (4.5) | — | ✅ |
| 0.3 | Equipe: fechar pendências de anotação e planejar a rodada 2 com contexto | — | ✅ |
| 0.4 | Medições: siglas ambíguas, taxa de listas e blocos repetidos, volume diário real, latência do qwen3.5:9b no servidor de produção (CPU ou GPU) | — | ✅ |
| 0.5 | Iterar o prompt no harness (few-shot, neutro como padrão, qwen, contexto) até bater o critério de 4.5 no `validation` | 0.2 | — |

### Fase 1 — Correções imediatas no léxico e no matcher (em paralelo com a Fase 0)

| # | Entrega | Resolve |
|---|---|---|
| 1.1 | Regras de sigla + `entity_rules.yaml` + normalização de espaços | C2, N5 |
| 1.2 | Máscara de nomes próprios e tabelas no léxico | C3 |
| 1.3 | Ticket DBA: PSTU, aliases de Novo e Missão | N6 |
| 1.4 | Corrigir o dicionário de dados | N10 |

Sobe `model.version` para 2.

### Fase 2 — Schema e leitores (coordenação entre times; bloqueia as Fases 3 a 5)

| # | Entrega | Dono |
|---|---|---|
| 2.1 | DDL parte A (aditiva): `tabela_artigo`, `tabela_paragrafo`, `tabela_execucao_classificacao`, colunas novas, execução legado, view | DBA (Gustavo) |
| 2.2 | API lê `vw_classificacao_vigente` | api-prisma |
| 2.3 | Analyser lê a view, aceita `schema_version` 1 ou 2, ignora `nao_relacionado`, tira a URL do prompt | analyser-prisma |
| 2.4 | Scraper envia `description` (linha fina) e o publisher ganha `--limit` | scrapping-prisma |

2.2 e 2.3 **precisam estar em produção antes** de o classificador gravar qualquer execução que não seja a legado.

### Fase 3 — O classificador grava o novo modelo de dados (sequencial)

| # | Entrega |
|---|---|
| 3.1 | `seg-v2` (tipos de bloco, C5, hashes), validado no harness |
| 3.2 | Gravar artigo, parágrafos, execução e `indice_paragrafo`; idempotência por execução; transação curta; outbox por execução. Em seguida, DDL parte B |
| 3.3 | Notificação v2 atrás da flag, ligada quando o Analyser confirmar |
| 3.4 | Backfill: o scraper republica o histórico com `pipeline_version='v2'` em lotes, classificado pelo léxico |

### Fase 4 — Classificador plugável (4.1 pode começar junto com a Fase 3)

| # | Entrega | Depende de |
|---|---|---|
| 4.1 | Interface + adaptador do léxico (teste que garante que nada muda) | — |
| 4.2 | Cliente Ollama, validação, prompt v1, consumidor com thread, pausa e requeue | 4.1, 0.4 |
| 4.3 | Modo sombra | 3.2, 4.2 |
| 4.4 | Job de marcação de blocos repetidos | 3.2 |

### Fase 5 — Decidir e trocar

1. Rodar o `test` uma vez com o prompt escolhido e anexar o relatório ao PR.
2. 1 a 2 semanas em sombra, com amostragem humana semanal.
3. Se o critério de 4.5 for atendido: PR que troca `metodo_vigente`, e depois `reclassify.py --promover` no histórico, com taxa limitada. O Analyser invalida e refaz os resumos.
4. Se não for atendido: o léxico corrigido continua vigente e seguimos para o passo 2 (encoder) antes de trocar.

### Fase 6 — Passo 2: encoder + cascata (depois da promoção ou em paralelo à sombra)

1. O LLM pré-rotula de 10 a 20 mil pares. Humanos revisam **às cegas** (sem ver o rótulo do LLM) os casos incertos e uma amostra aleatória.
2. Fine-tunar **BERTimbau**, **Albertina PT-BR** e **laya-multilingual** no mesmo split, com a entidade-alvo marcada no texto. Comparar macro-F1, calibração (ECE, com temperature scaling) e a taxa de casos repassados ao LLM.
3. Cascata: o encoder classifica tudo (~20 a 60 ms por par em CPU) e o LLM só atende quando a confiança calibrada estiver abaixo do limiar. Passa pelo mesmo gate e pelo mesmo modo sombra.
4. O gold original de ~1.300 pares continua **intocado como teste**.

### Resumo de dependências externas

| Time | O que precisa fazer |
|---|---|
| DBA | DDL em duas fases, versão do Postgres, PSTU e aliases |
| API | Ler a view; avisar que a base da barra muda |
| Analyser | Aceitar a v2 e ler a view, usar título e vizinhos, invalidar resumos na promoção, tirar a URL do prompt |
| Scraper | `description`, `--limit`, republicação do histórico; correção de R1 na origem |
| Infra | Ollama dedicado ou compartilhado, GPU, runner de CI com Postgres, `consumer_timeout` do RabbitMQ |
| Anotadores | Pendências e rodada 2 com contexto; revisão semanal da produção |

---

## 6. Riscos

- **Precisão das abas:** mesmo o melhor modelo erra. O que protege a página é o gate com intervalo de confiança mais o modo sombra, não o modelo em si.
- **Amostra pequena de teste** (21 negativos e 14 positivos): as diferenças entre prompts podem ser ruído. Ampliar o gold (rodada 2) é parte do plano, não um extra.
- **Viés do LLM com nomes de políticos:** trocar o nome muda o sentimento (Elbouanani et al., ACL 2025). Medir a precisão por entidade no harness, e não só a média geral.
- **Vazão em CPU:** um 7–9B em CPU leva de 4,5 a 10 s por parágrafo. Dependendo do volume real, só com GPU ou só com o encoder (Fase 6).
- **Mudança visível:** a promoção muda a barra e os panoramas históricos. É preciso comunicar e versionar.
- **Direitos e LGPD:** guardar a matéria segmentada no `app_db2` amplia o conteúdo armazenado. Validar com o time.

---

## 7. Resposta direta às duas anotações

- **"Enviar título, manchete, parágrafo anterior e posterior para contexto do Analyser":** sim.
  - Título e ordem dos parágrafos: já dá para fazer no classificador (4.2, Fases 2 e 3).
  - Manchete / linha fina: depende de uma mudança pequena no scraper, porque o campo já é extraído e só é descartado.
  - Os vizinhos incluem parágrafos sem entidade.
- **"Modelo classificando ou regra + modelo corretor?":** **o modelo classificando.** O corretor tem teto de recall (medido: 0,38 contra 0,53) e sofre ancoragem (literatura). A regra fica como fallback e linha de base. O destino é encoder fine-tunado + LLM na incerteza. A troca em produção só acontece depois do gate e do modo sombra, porque o modelo pronto ainda marca sentimento demais.

---

## 8. Checklist de "pronto"

- [ ] Harness e baseline no CI
- [ ] C2, C3, N5, N6 corrigidos e com teste
- [ ] DDL A aplicada; API e Analyser lendo a view
- [ ] Classificador gravando artigo, parágrafos, execução e índice
- [ ] Notificação v2 ligada; Analyser usando título e vizinhos
- [ ] LLM em sombra por 2 semanas, com amostragem humana
- [ ] Gate atingido e promoção feita
- [ ] Rodada 2 de anotação concluída
- [ ] Encoder avaliado (BERTimbau, Albertina, Laya) e decisão sobre a cascata

---

## 9. Materiais de apoio

Na pasta de trabalho desta sessão (temporária, copiar se quiser guardar):
- `scratchpad/piloto/`: scripts (`prep.py`, `run.py`, `metrics.py`), unidades e saídas por estratégia (`out_*.jsonl`), prompts.
- `scratchpad/pesquisa/PESQUISA.md`: pesquisa bibliográfica com links (arquitetura, sentimento por alvo, LLMs pequenos, custo, dados).
- `scratchpad/pesquisa/JEV_LAYA.md`: pesquisa sobre Jev e Laya.

Referências principais:
- Seno et al., PROPOR 2024 — https://aclanthology.org/2024.propor-1.32.pdf
- Kapetanovic et al., CIKM 2026 — https://arxiv.org/abs/2608.25869
- Huang et al., ICLR 2024 — https://arxiv.org/abs/2310.01798
- Golubev et al., RuSentNE-2023 — https://arxiv.org/abs/2305.17679
- Hamborg & Donnay, NewsMTSC — https://aclanthology.org/2021.eacl-main.142/
- Bucher & Martini 2024 — https://arxiv.org/abs/2406.08660
- Chatterjee et al., Frontiers AI 2026 — https://www.frontiersin.org/journals/artificial-intelligence/articles/10.3389/frai.2026.1844418/xml
- Elbouanani et al., ACL 2025 — https://arxiv.org/abs/2505.19776
- Ibrahim & Zaki (Jev em ciências sociais computacionais) — https://arxiv.org/abs/2609.24574
- Laya — https://github.com/NandhaKishorM/laya · https://huggingface.co/convaiinnovations/laya-multilingual
