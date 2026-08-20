# Pendências técnicas encontradas durante a anotação manual

## Regras de anotação a incorporar em docs/protocolo-anotacao.md (depois, não agora)

Esclarecimentos que surgiram lendo linha por linha com a Bea, complementares aos exemplos que já estão no protocolo:

1. **Autocitação/relato da própria pessoa não é elogio nem crítica de terceiro.** Se a própria entidade cita fatos favoráveis sobre si mesma (ex.: "reabertura de fábricas" citada pelo próprio Alckmin, que geriu a política), ou descreve sua própria ação/fala, é `neutro` -- só conta quando ALGUÉM DE FORA avalia explicitamente.
2. **Só `positivo`/`negativo` quando há julgamento explícito no texto** -- uma palavra/frase de avaliação (elogio, crítica, "fracasso", "sucesso", "medíocre"), não um fato que o leitor PODE interpretar como bom ou ruim. Regra geral confirmada várias vezes pela Bea.
3. **Sigla de partido entre parênteses, quando a crítica/elogio é claramente à pessoa, não ao partido** -- `neutro` para a entidade-partido (ver item 3 abaixo, já detalhado).
4. **Resultado de apuração/investigação relatado sem palavra de julgamento = `neutro`**, mesmo quando contradiz um relato anterior (ex.: "não identificou indícios de procedimento irregular"). Só vira `negativo`/`positivo` quando alguém expressamente critica ou elogia o resultado.
5. **Reação explícita de plateia/público conta** (aplausos, vaias, ser "contestado aos gritos") -- é uma avaliação de terceiro (o público), mesmo sem uma frase com sujeito claro.
6. **Comparação metafórica explícita com uma figura amplamente positiva (ex.: comparar alguém a "o Pelé" de algo) conta como elogio de terceiro**, mesmo com hedging ("é difícil comparar, mas...").


Achados ao ler linha por linha com a Bea (2026-08-20), ainda não implementados -- registrar aqui pra não perder, consolidar em `config/entities.yaml`/ADR quando a rodada de anotação terminar.

## 1. `config/entities.yaml`: alias "Bolsonaro" da entidade `jair-bolsonaro` também casa com Flávio Bolsonaro

Confirmado: parágrafo `24-0` ("Com colete à prova de balas, Renan Santos abre campanha e ataca Lula e Flávio Bolsonaro") nunca menciona "Jair Bolsonaro", só "Flávio Bolsonaro" -- mas gerou uma linha para a entidade `jair-bolsonaro` mesmo assim. Causa provável: `jair-bolsonaro` tem "Bolsonaro" (sozinho) como um dos aliases, que casa por match exato em qualquer menção a "Flávio Bolsonaro" também (mesmo sobrenome). Risco já previsto em `context.md` raiz, seção 7 ("erros de identificação de entidades com nomes semelhantes... políticos de mesmo sobrenome").

Ajuste sugerido (não aplicado ainda): remover "Bolsonaro" sozinho dos aliases de `jair-bolsonaro`, manter só formas que não colidem com Flávio (ex.: "Jair Bolsonaro", "ex-presidente Bolsonaro", "presidente Bolsonaro").

## 2. PSB não está na lista curada de entidades

`config/entities.yaml` tem 10 entidades (lula, jair-bolsonaro, geraldo-alckmin, fernando-haddad, tarcisio-de-freitas, marina-silva, pt, pl, psol, uniao-brasil) -- **PSB não está entre elas**, mesmo sendo o partido do próprio vice-presidente Geraldo Alckmin e aparecendo com frequência nos artigos (ex.: `250-1`: "O PSB oficializou... a candidatura do vice-presidente Geraldo Alckmin"). Curadoria a fazer: adicionar PSB (e revisar se falta algum outro partido relevante) em `config/entities.yaml`.

## 3. Atribuição: sigla de partido entre parênteses ≠ avaliação do partido

Achado lendo `24-2` [Partido Liberal]: "Renan Santos... focando nas críticas... ao senador Flávio Bolsonaro (PL)". O match técnico está correto (o texto cita "PL"), mas a crítica é dirigida ao Flávio como pessoa -- o "(PL)" é só a filiação partidária entre parênteses, não uma avaliação do partido em si. Não é bug de matching (o entity_matcher fez o trabalho dele, achar a menção); é uma orientação de anotação: quando a sigla do partido aparece só como identificação do político citado, e a crítica/elogio é claramente à pessoa, classificar `neutro` para a entidade-partido (mesmo padrão de "crítica à medida vs. crítica à pessoa" do protocolo, estendido pra "crítica à pessoa vs. ao partido dela"). Vale um exemplo assim no `docs/protocolo-anotacao.md` quando for atualizado.

## 4. RESOLVIDO (não é bug): "só reconhece 1 entidade" era o round-robin, não o matcher

A Bea notou o padrão "texto menciona 3 entidades, só 1 vira linha" enquanto respondia a planilha. Investigados dois casos (`24-1` e `261-1`): em ambos o `entity_matcher` encontrou TODAS as entidades do parágrafo corretamente (`261-1`, por exemplo, gerou 6: Lula, Jair Bolsonaro, Alckmin, PT, PL, PSOL). O que a Bea via como "sumiço" era o **round-robin do `scripts/build_annotation_batches.py`**: cada revisor recebe só uma fatia das entidades de um mesmo parágrafo (exceto as de sobreposição, que vão pra todo mundo) -- ver `data/annotation/manifest.csv`, coluna `reviewers`, pra conferir quem ficou com qual entidade de um parágrafo. Não precisa de ajuste; é o comportamento pretendido pra dividir o volume entre os 6.

## 5. BUG confirmado: fuzzy match falso positivo de "União Brasil" em "no Brasil"

Prioridade alta -- pode estar afetando muitas linhas no corpus inteiro, não só uma. Confirmado: `_fuzzy_match_score` (`src/entity_matcher.py`) casa o alias "União Brasil" contra a janela de 2 palavras "no Brasil" com score 85.7% (`fuzz.ratio`), acima do `fuzzy_threshold` de 0.85 (`config/settings.yaml`). Como "no Brasil"/"do Brasil"/"o Brasil" são frases extremamente comuns em qualquer notícia política brasileira, isso provavelmente está gerando muitas linhas espúrias de "União Brasil" (`nao_relacionado` na prática) espalhadas pelo dataset inteiro. Caso concreto: parágrafo `289-4` (fala sobre "estrela", "poder", "Brasil" -- nunca menciona União Brasil).

Ajuste sugerido (não aplicado ainda): subir o `fuzzy_threshold` especificamente pra aliases curtos/genéricos tipo "Brasil" dentro de "União Brasil", ou exigir que pelo menos uma palavra "forte" do alias (ex.: "União") também apareça na janela candidata antes de aceitar o fuzzy match -- decisão de desenho, discutir antes de implementar.

## 6. Suspeita de segundo falso positivo: "presidente" sozinho casando com Lula

Parágrafo `4-7`: "A presidente da Petrobras, Magda Chambriard, afirmou..." -- nunca menciona Lula, mas gerou uma linha para a entidade `lula`. Ainda não investigado a fundo (diferente do item 5, que foi confirmado com teste direto) -- hipótese: fuzzy match de "presidente Lula" contra uma janela que inclui "presidente" sozinho + palavra seguinte, ou algum alias curto demais. Vale rodar o mesmo tipo de teste do item 5 antes de decidir o ajuste.

## 7. Suspeita (não confirmada) de entidade não reconhecida em algum parágrafo

Item genuinamente em aberto (diferente do item 4, que foi fechado): se aparecer um caso onde uma entidade mencionada no texto comprovadamente não gera NENHUMA linha em lugar nenhum (nem no manifest completo, não só no arquivo de um revisor específico), registrar aqui com o `paragraph_id` exato.
