# Continuidade do experimento

**Atualizado em 12/11/2026.** Este resumo permite que outro modelo ou colaborador retome o trabalho. Leia também o [plano de implementação](plano-implementacao-experimentos.md), as [decisões metodológicas](decisoes-experimentos.md) e o [protocolo de anotação](protocolo-anotacao-v2.md).

## Estado atual

- Ferramentas de gabarito, métricas, concordância, menções, léxico e TF-IDF estão implementadas. Uma execução anterior registrou **77 testes aprovados** em `.venv-experiments`. O TF-IDF só foi exercitado com dados sintéticos; não houve treino no gabarito final.
- `.venv-experiments` é isolado do ambiente principal e tem scikit-learn instalado. A seleção de notícias não deve ser regenerada sem necessidade: seus arquivos e metadados preservam a proveniência histórica de agosto.
- O snapshot `experiments/data/entidades.yaml` foi exportado de `prisma-dev` em modo somente leitura: 40 entidades e 73 aliases. Seu conteúdo coincide com o snapshot do piloto; o arquivo histórico não foi sobrescrito.
- Bea decidiu continuar com os dados disponíveis em `data/raw/articles_202608191040.csv` (19/08/2026), sem aguardar nova raspagem ou export. Essa fonte foi escolhida para prosseguir.
- Bea executou o comando e gerou a proposta `experiments/data/preparacao-revisada-20261112/`, com 60 IDs únicos, 90 atribuições e 20 substituições propostas. Está marcada **PROVISÓRIA** e `scope_review` está **PENDENTE**; a amostra não está congelada nem é final.
- Bea decidiu manter a amostra atual como está. As sete notícias fora do escopo (`445, 110, 325, 358, 354, 333, 390`) permanecem; `306` é ambígua; `408, 435, 406, 418, 457` têm foco misto; `373` trata de PT, que está no escopo. Essas limitações estão aceitas para esta rodada.
- Alertas de integridade: `178` tem bloco repetido junto de `445`; `139` tem texto interrompido e reiniciado; `57` e `501` têm mojibake; `220` contém lista “Mais lidas”. Melhorias futuras estão em [`data-quality-improvements.md`](../experiments/data/preparacao/data-quality-improvements.md). Não corrigir textos correntes após gerar planilhas, pois isso altera offsets.
- Os cinco casos de foco misto (`408, 435, 406, 418, 457`) continuam registrados como limitação; `373` trata do PT e está no escopo.
- O relatório de qualidade está em [`data-quality-improvements.md`](../experiments/data/preparacao/data-quality-improvements.md). A saída proposta (60 IDs únicos, 90 atribuições) permanece PROVISÓRIA; `scope_review` está PENDENTE e a anotação ainda não começou.

## Regras e decisões

- **D10 — partidos:** nome completo conta; nomes que também são palavras comuns, como “Missão”, exigem a palavra “partido” antes; siglas exigem artigo anterior, incluindo contrações; siglas entre parênteses são ignoradas.
- **D11 — pronomes:** atribuir o pronome admitido à entidade anterior mais próxima na notícia, incluindo partidos. Nas planilhas do piloto, `145-6` continua como Flávio, conforme as respostas humanas originais, embora a heurística de proximidade preveja Lula.
- **D12 — normalização:** remover acentos e substituir pontuação por separadores somente na representação auxiliar de reconhecimento; preservar texto original e offsets.
- **D15 — referências genéricas de partido:** “o partido”, “a legenda” e “a sigla” não são menções de PT nem de qualquer outro partido e não geram teor. D15 revoga essa parte de D9; continuam valendo os critérios de D10 para nome completo, palavra comum precedida por “partido”, sigla com artigo/contração e exclusão de siglas entre parênteses.

## O que falta para o gabarito consensual

As seis planilhas existentes são do **piloto**, não da amostra definitiva. Ainda não existem planilhas definitivas, nem respostas da amostra nova para adjudicar. Próximas etapas, nesta ordem:

1. Gerar as planilhas por anotador com os 15 IDs de cada `ids_<nome>.txt`, usando `experiments/data/entidades.yaml` e `data/raw/articles_202608191040.csv`. Manter os candidatos e textos atuais sem alterações.
2. Validar cada planilha preenchida independentemente. Não presumir que os seis anotadores estão disponíveis; confirmar participação e distribuição antes.
3. Nas seis notícias compartilhadas, comparar menções e teor, adjudicar divergências e registrar as decisões.
4. Consolidar e validar o gabarito consensual, preservando respostas independentes e histórico de alterações.
5. Congelar o split planejado de 39/6/15 por notícia e guardar os hashes. Depois vêm treino dos modelos e avaliação final no teste congelado.

Assim, “adjudicação” não significa que falta corrigir as planilhas do piloto: significa resolver diferenças entre as futuras anotações da amostra definitiva, principalmente nas seis notícias compartilhadas, para formar o gabarito.

## Instrução para a próxima sessão

Bea pediu que, quando uma etapa exigir executar código, o assistente forneça o comando exato para ela rodar e aguarde a saída; não executar código ou testes em nome dela. A próxima etapa é gerar as planilhas por anotador usando os 15 IDs de cada `ids_<nome>.txt`, o snapshot `experiments/data/entidades.yaml` e o CSV `data/raw/articles_202608191040.csv`. Bea decidiu manter a amostra atual sem substituir candidatos nem corrigir textos; as limitações estão documentadas em [`experiments/data/preparacao/data-quality-improvements.md`](../experiments/data/preparacao/data-quality-improvements.md). `scope_review` permanece PENDENTE e a anotação ainda não começou. Não chamar o gabarito ou split de final.
