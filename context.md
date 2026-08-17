# Contexto do Repositório — Classifier

## 0. Relação com o contexto geral do projeto

Este repositório implementa o componente **`Classifier`** da plataforma Prisma (análise de percepções sobre políticos e partidos). A visão completa do produto — objetivo geral, escopo, arquitetura de todos os componentes, regras de classificação, riscos, avaliação de modelos, requisitos não funcionais e as orientações gerais para agentes — vive em `context.md` na raiz de `Lab-inovação/` (repositório irmão de `scrapping-prisma` e `classifier-prisma`) e é a **fonte da verdade**.

Este documento não duplica aquele conteúdo. Ele recorta o que é responsabilidade deste repositório e registra decisões e observações específicas daqui. **Em caso de conflito, o `context.md` raiz prevalece.**

Referências usadas neste documento apontam para as seções do `context.md` raiz (ex.: "seção 9" = seção 9 do contexto geral).

## 1. Responsabilidade deste repositório

Segundo a seção 7.4 do contexto geral, o `Classifier` é responsável pela análise **individual** de cada conteúdo coletado pelo scraper:

- validar se o texto possui conteúdo suficiente;
- normalizar texto e metadados;
- identificar idioma e tipo de conteúdo;
- detectar duplicatas ou similaridade relevante;
- identificar políticos e partidos mencionados;
- separar os trechos relevantes para cada entidade;
- classificar cada trecho ou manifestação como neutro, negativo ou positivo;
- registrar confiança, incerteza e evidências;
- persistir os resultados individuais no `System DB`;
- publicar um evento para o `Analyser` após a classificação.

**Não é responsabilidade deste repositório**: coletar conteúdo (isso é do `scrapping-prisma`), gerar análise agregada por entidade (`Analyser`), expor API pública, nem apresentar resultado em interface — e, por decisão de escopo do produto (seção 6.2), **não é** classificar viés político ou posicionamento ideológico.

O `Classifier` também não deve gerar sozinho uma conclusão geral sobre a reputação da entidade — produz análises individuais que alimentam a etapa agregada.

## 2. Estado atual

Repositório recém-criado: só há `.git` e um `README.md` vazio. Nenhuma decisão de stack, modelo ou schema foi implementada ainda — tudo abaixo em "decisões em aberto" (seção 9) está genuinamente aberto.

O `scrapping-prisma` (repositório irmão) já está funcional e é a referência real de como esse pipeline entrega trabalho para cá.

## 3. Ponto de integração com o Scraper — DECIDIDO: RabbitMQ, com `analysis_jobs` como outbox interno do scraper

**Decisão tomada em 16/08 (validada por análise de arquitetura), registrada aqui para não ser reaberta sem motivo.** O projeto vai usar **RabbitMQ** como broker de mensagens entre Scraper e Classifier — infraestrutura sendo configurada externamente a este repositório.

Isso muda o desenho anterior deste documento, que cogitava o Classifier consumir a fila `analysis_jobs` direto do Postgres do scraper. Essa ideia foi descartada: ela contrariava uma regra que o próprio `context.md` raiz já define (seção 7.2 — "Scraper DB mantém separação lógica"; seção 19.2 — "nenhum componente deve acessar diretamente o banco... de outro"). Vale registrar o porquê, porque não é óbvio:

- a tabela `analysis_jobs` (implementada no `scrapping-prisma`, com trigger + `src/jobs_store.py` + `FOR UPDATE SKIP LOCKED`) já resolve bem o problema de concorrência entre workers — então **RabbitMQ não está entrando para resolver concorrência**, e sim para desacoplar os dois bancos;
- **`analysis_jobs` não deve ser descartada.** Ela muda de papel: deixa de ser "fila que um worker externo consome" e passa a ser um **outbox transacional interno ao `scrapping-prisma`** — continua gravando via trigger, dentro da mesma transação que marca o artigo como `success` (garantia que não se replica de graça publicando direto no código);
- o `scrapping-prisma` ganha uma peça nova e pequena — um **publisher** que lê `analysis_jobs` (reaproveitando `claim_next_job`) e publica cada job no RabbitMQ, com o **conteúdo do artigo embutido no payload** (texto, título, `canonical_link`, `source_name`, `published_at`, `article_id`, `job_id`, `pipeline_version`) — não só o ID, para o Classifier nunca precisar voltar ao Postgres do scraper para buscar dado;
- ao publicar com sucesso, o publisher marca o job com um novo status (nome ainda a decidir — ex. `published`, evitando reusar `completed` com dois significados diferentes); falha de publicação usa o retry (`attempts`/`max_attempts`) que já existe e já é testado.

**Regra que este repositório deve seguir, sem exceção**: o Classifier **nunca tem connection string para o Postgres do scraper**. Ele só fala com o RabbitMQ.

Antes de implementar o consumidor aqui, é preciso alinhar com quem está configurando o RabbitMQ (fora deste repositório) um contrato mínimo: nome da fila/exchange, formato exato do payload (com campo de versão do schema), semântica de ack, política de retry/dead-letter. Sem isso combinado, o risco é o broker ser configurado com uma topologia que não bate com o que o Classifier espera.

## 4. Contrato de saída esperado

Com base nas seções 7.5 (System DB) e 13 (rastreabilidade) do contexto geral, cada resultado individual persistido pelo Classifier deve cobrir, no mínimo:

- entidade(s) política(s) identificada(s) no conteúdo;
- trecho(s) analisado(s), associado(s) a cada entidade (um conteúdo pode ter múltiplas entidades com categorias diferentes — seção 5);
- categoria (`neutro` / `negativo` / `positivo`) por trecho/entidade;
- confiança ou incerteza, quando a técnica permitir;
- evidência: o trecho literal que sustenta a classificação (nunca só o rótulo);
- modelo, versão e data/duração da execução;
- estado de processamento, permitindo reprocessamento sem apagar histórico (seção 19.6).

Depois de persistir, o Classifier deve publicar um sinal para o `Analyser`. Mecanismo exato (evento, outra tabela de fila, etc.) ainda não definido — ver seção 9.

## 5. Regras de classificação que este repositório deve seguir

Resumo operacional (detalhamento completo nas seções 9, 10 e 11 do contexto geral):

- classificar o **teor em relação a uma entidade-alvo**, nunca o documento inteiro como um todo quando houver múltiplas entidades ou posicionamentos conflitantes;
- `neutro` não é ausência de palavras positivas/negativas — exige considerar contexto, alvo, negação, citação e ironia;
- alegação negativa ou elogio deve ser atribuído à fonte/autor (`"o conteúdo afirma..."`), nunca apresentado como fato verificado pelo sistema;
- não usar o posicionamento conhecido da fonte como atalho para decidir a categoria;
- não forçar categoria quando não houver evidência suficiente — incerteza deve ser registrada, não escondida;
- tratar separadamente entidades diferentes presentes no mesmo conteúdo;
- considerar citações, negações, ironia, comparações e opiniões de terceiros;
- permitir reprocessamento e revisão humana sem apagar silenciosamente o histórico.

## 6. Avaliação de modelos

Este repositório é o candidato natural a comparar abordagens (regras/léxico, ML tradicional, LSTM, transformers, LLM, híbrido — seção 15 do contexto geral não elege vencedor a priori). Antes de escolher uma técnica definitiva:

- estabelecer uma baseline simples (regra/léxico) para servir de referência de comparação, não só para os modelos "sofisticados" competirem entre si;
- montar conjunto de referência com protocolo de anotação explícito, registrando casos ambíguos e divergência entre anotadores;
- avaliar por precisão/revocação/F1 por categoria, macro-F1, matriz de confusão, acerto na identificação do alvo (entidade correta), calibração de confiança, latência e custo — não só acurácia geral.

## 7. Riscos específicos deste componente

A maior parte dos riscos da seção 14 do contexto geral é diretamente responsabilidade do Classifier:

- classificação incorreta de ironia, sarcasmo, negação e citações;
- dificuldade de identificar corretamente o alvo (a quem a opinião se refere) quando há múltiplas entidades;
- confusão entre crítica a uma medida/decisão e crítica ao político em si;
- confusão entre notícia sobre evento negativo e avaliação negativa da entidade (ex.: político é vítima de algo vs. político é criticado por algo);
- erros de identificação de entidades com nomes semelhantes (homônimos, políticos de mesmo sobrenome, partido com sigla parecida);
- viés do conjunto de treinamento e do próprio modelo;
- sínteses ou classificações que amplifiquem uma minoria ruidosa.

Como o produto será público e monetizado (seção 1.1 e 20 do contexto geral), um erro de classificação aqui tem custo direto de contestação/reputação — reforça a importância da seção 6 (avaliação) antes de expor qualquer resultado publicamente.

## 8. Requisitos não funcionais aplicáveis aqui

Da seção 16 do contexto geral, os que mais pesam neste repositório:

- **Idempotência**: reprocessar o mesmo job não pode gerar resultado duplicado nem reenviar evento duplicado ao Analyser;
- **Reprodutibilidade**: versionar modelo, regras, prompts (se usar LLM) e conjunto de avaliação — um resultado antigo precisa ser explicável com o modelo/versão que o gerou;
- **Auditabilidade**: todo resultado rastreável até o job de origem e a evidência textual;
- **Segurança**: se a abordagem envolver API de LLM externa, nunca hardcode a chave — variável de ambiente, nunca commitada; e avaliar se enviar o texto do artigo a um serviço terceiro é aceitável (o conteúdo sai do ambiente controlado — ver LGPD, seção 16 do contexto geral);
- **Eficiência de custo**: se a técnica escolhida for uma API paga por token, o custo por classificação importa dado que a monetização por anúncio (seção 20.1) gera receita modesta por usuário.

## 9. Decisões em aberto específicas deste repositório

Nenhuma destas está decidida — não assumir nenhuma sem validar:

- técnica de classificação (regras/léxico, ML tradicional, LSTM, transformer ajustado, LLM, híbrido);
- linguagem/stack de implementação (Python é o caminho natural por reaproveitar padrões e eventualmente bibliotecas do `scrapping-prisma`, mas isso é uma sugestão, não decisão tomada);
- estratégia de identificação e resolução de entidades (lista fixa de políticos/partidos mantida manualmente? NER genérico? fuzzy matching de nome?);
- unidade de segmentação do texto (sentença, parágrafo, janela deslizante) — seção 18 do contexto geral;
- mecanismo de publicação do evento para o Analyser (a fronteira Scraper→Classifier já está decidida como RabbitMQ — seção 3 — mas Classifier→Analyser segue em aberto);
- modelo local vs. API de terceiro, e as implicações de custo/latência/privacidade que isso traz (seção 8).

Ao propor resolver qualquer um desses pontos, apresentar justificativa, alternativas, riscos e impacto — conforme seção 19.1 (item 10) e seção 18 do contexto geral.

## 10. Orientações para quem implementar este repositório

Aplicam-se integralmente as seções 19.2 a 19.6 do contexto geral (arquitetura/código limpo, segurança da informação, testes, documentação, rastreabilidade). Pontos que merecem destaque específico para este repositório:

- **Padrão de repositório irmão**: o `scrapping-prisma` é a referência de estilo já validada no projeto — SQL direto sem ORM, migrations numeradas com script de rollback documentado em comentário, testes rodando contra banco isolado (`POSTGRES_TEST_DB`), README completo com arquitetura, como rodar e limitações conhecidas. Seguir esse padrão aqui, a menos que a técnica de classificação escolhida exija outra coisa (ex.: serialização de modelo, biblioteca de NLP/ML) — nesse caso, justificar o desvio.
- **Baseline antes de complexidade**: implementar primeiro uma abordagem simples (regra/léxico) funcionando ponta a ponta no pipeline antes de investir em modelo mais complexo — permite validar o contrato de dados (seção 4) e a integração (seção 3) cedo, e dá uma referência de comparação real para a seção 6.
- **Teste de idempotência do consumidor** é obrigatório antes de considerar a integração com o scraper pronta (seção 19.4 do contexto geral).
- **Nunca perder o vínculo** job → resultado → evidência, mesmo em caso de falha ou reprocessamento.
