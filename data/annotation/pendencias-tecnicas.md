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

## 5. RESOLVIDO: fuzzy match falso positivo de "União Brasil" em "no Brasil"

Corrigido em `fix: corrige fuzzy match de string inteira em aliases multi-palavra` (2026-08-20). Causa raiz: `_fuzzy_match_score` comparava a janela inteira concatenada contra o alias inteiro via `fuzz.ratio` -- uma palavra-âncora longa do alias ("União") dominava o score de caracteres, deixando "Brasil" (a palavra curta) quase irrelevante. "no Brasil" batia 85.7% contra "União Brasil", acima do `fuzzy_threshold` de 0.85. Corrigido comparando palavra por palavra (mesma posição no alias e na janela) e usando o mínimo dos scores -- um conector genérico no lugar da palavra de verdade agora derruba o score. Testes de regressão em `tests/test_entity_matcher.py`.

## 6. RESOLVIDO: mesmo bug também afetava "presidente Lula" em "presidente da"

Mesma causa raiz do item 5, mesmo fix. Confirmado com o parágrafo `4-7` ("A presidente da Petrobras, Magda Chambriard, afirmou...") -- gerava uma linha falsa para `lula` (fuzzy, score 0.857, alias "presidente Lula") sem nunca mencionar Lula. Esse era o caso mais grave dos dois: "presidente da/na [qualquer coisa]" é uma construção comum demais em notícia política pra ser um falso positivo raro. Resolvido pelo mesmo fix do item 5, com teste de regressão dedicado.

## 7. Suspeita (não confirmada) de entidade não reconhecida em algum parágrafo

Item genuinamente em aberto (diferente do item 4, que foi fechado): se aparecer um caso onde uma entidade mencionada no texto comprovadamente não gera NENHUMA linha em lugar nenhum (nem no manifest completo, não só no arquivo de um revisor específico), registrar aqui com o `paragraph_id` exato.

## 8. Desempate de aliases da mesma entidade depende da ordem do YAML (baixa prioridade)

Investigado (2026-08-20), não corrigido de propósito -- é dívida pequena, não bug urgente. Em `match_entities`, quando dois aliases da MESMA entidade empatam no score fuzzy, o código mantém o primeiro candidato encontrado (`fuzzy_score > best.score` é estritamente maior, não `>=`) -- ou seja, quem "ganha" depende só da ordem em que os aliases aparecem em `config/entities.yaml`, não de uma regra de especificidade.

Confirmado que acontece de verdade: 49 ocorrências no CSV real, causadas por mojibake (`TarcÃsio` vs `Tarcísio`) fazendo `Tarcisio`/`Tarcísio`/`Tarcísio de Freitas`/`Tarcisio de Freitas` empatarem no mesmo parágrafo -- nesses casos o alias mais específico venceu por coincidência de ordem no YAML. Em cenário sintético ("presidente Lulla"), o MENOS específico ("Lula") venceu, porque "presidente Lula" vem depois na lista de aliases.

Impacto: nenhum na classificação em si (`entity_id`, `category`, `confidence`, `paragraph_index` não mudam) -- só o campo de auditoria `entity_matched_alias` (`classifications`, ver comentário na migration 0003) registra o alias "errado". Quem audita ainda vê a entidade certa, o trecho completo (`evidence_text`) e o score, então dá pra confirmar manualmente mesmo assim -- é qualidade de auditoria, não integridade de dado.

Ajuste sugerido quando for feito: ao empatar, preferir o alias mais longo/específico em vez de manter o primeiro -- não depender da ordem do YAML, que é frágil (o próximo alias adicionado pode inverter o resultado "por sorte").
