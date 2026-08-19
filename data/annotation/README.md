# Dataset de anotação — treino/validação/teste + lotes por revisor

Gerado por [`scripts/build_annotation_batches.py`](../../scripts/build_annotation_batches.py) a partir de [`data/raw/articles_202608191040.csv`](../raw/articles_202608191040.csv) (dump de `articles` do `scrapping-prisma`, extração bem-sucedida). Reaproveita a segmentação e o matching de entidade **reais** do pipeline (`src/segmentation.py`, `src/entity_matcher.py`, `config/entities.yaml`) — as unidades aqui são exatamente o que o Classifier geraria em produção.

472 artigos no CSV têm extração bem-sucedida, 471 com pelo menos uma entidade identificada — mas **esta rodada usa só uma amostra de 150 deles** (ver "Rodada 1 vs. rodada 2" abaixo), não o corpus inteiro. Números desta geração:

| Split | Artigos | Unidades (parágrafo, entidade) |
|---|---|---|
| `train` | 105 | 1432 |
| `validation` | 22 | 302 |
| `test` | 23 | 276 |
| **Total (rodada 1)** | **150** | **2010** |

Os outros 321 artigos elegíveis ficam reservados pra uma rodada 2.

## Método

- **Split por artigo, não por parágrafo** (70/15/15) — evita vazar parágrafos do mesmo artigo entre treino e teste, o que inflaria artificialmente a métrica de avaliação.
- **Sobreposição de 10% dentro de cada split**, anotada pelos **6 revisores ao mesmo tempo** — existe só para medir concordância entre anotadores (`context.md` raiz, seção 15), não é dado de treino "em dobro". O restante (90%) é dividido em round-robin, disjunto, um revisor por linha.
- **Cada revisor recebe ~502 linhas**: ~201 de sobreposição (iguais para todo mundo) + ~301 exclusivas.
- **`split` não aparece nas planilhas dos revisores** (`revisor_1.csv` … `revisor_6.csv`) — quem anota não precisa saber (e não deveria saber) se está anotando treino, validação ou teste, tanto porque é irrelevante para a tarefa quanto para não introduzir viés.
- Determinístico via `SEED = 42` — rodar o script de novo sobre o mesmo CSV gera exatamente a mesma divisão.

## Rodada 1 vs. rodada 2

A granularidade correta (parágrafo real, não o artigo inteiro — ver nota abaixo) triplicou o volume total (1856 → 6434 unidades nos 471 artigos elegíveis). Pra não sobrecarregar os 6 revisores numa primeira rodada, `ROUND1_ARTICLE_COUNT = 150` (topo do script) usa só uma amostra — os artigos são embaralhados uma vez (`SEED = 42`) e a rodada 1 pega o prefixo dessa ordem; os 321 restantes ficam no mesmo lugar, prontos pra virar rodada 2. Pra ampliar depois, basta subir `ROUND1_ARTICLE_COUNT` e rodar o script de novo — os artigos da rodada 1 continuam exatamente os mesmos (é um prefixo maior da mesma ordem), então nada que já foi anotado precisa ser refeito ou reatribuído.

## Por que a granularidade real importa (achado, não só detalhe técnico)

O extrator do scraper (`trafilatura`) separa parágrafos com **uma quebra de linha simples**, não linha em branco. A primeira versão de `src/segmentation.py` só reconhecia linha em branco como fronteira — na prática, isso colapsava a maioria dos artigos em **um parágrafo só** (o artigo inteiro). Corrigido antes de gerar este dataset (ver commit correspondente em `src/segmentation.py`); sem o fix, seria impossível o objetivo básico da anotação por parágrafo: separar, dentro do mesmo artigo, um trecho que fala bem de uma entidade de outro que fala mal.

## Arquivos

- `manifest.csv` — visão completa (engenharia): `unit_id`, `article_id`, `paragraph_id`, `entity_id`/`entity_name`, `split`, `is_overlap`, `reviewers` (lista, separada por `;`), fonte, link, texto. **Não entregar aos revisores** — é o mapa pra reconciliar os resultados depois.
- `revisor_1.csv` … `revisor_6.csv` — o que cada revisor de fato recebe, no formato de [`docs/protocolo-anotacao.md`](../../docs/protocolo-anotacao.md): `paragraph_id`, `entity_name`, `paragraph_text`, `label` (a preencher), `intensity` (opcional, a preencher), `notes` (opcional).

## Depois que os revisores devolverem as planilhas

1. Juntar as respostas de volta usando `manifest.csv` como chave (`paragraph_id` + `entity_name` → `unit_id`).
2. Nas linhas de **sobreposição** (`is_overlap = true`), comparar os 6 rótulos da mesma unidade — calcular concordância (ex.: Fleiss' kappa, já que são 6 anotadores por linha) por categoria e registrar os casos de divergência, como o `context.md` exige — não esconder, catalogar.
3. Linhas marcadas `indeterminado` ou `não_relacionado` (ver protocolo) não viram rótulo de treino direto — decidir depois se tratam como classe própria ou são descartadas, com base em quantas caíram aqui.
4. Só depois disso o `test` (e o `validation`) devem ser tratados como "fechados" — não olhar o desempenho no teste antes de finalizar o modelo, senão ele deixa de ser um teste de verdade.

## Como regenerar

```bash
.venv/bin/python scripts/build_annotation_batches.py
```

Sobrescreve `manifest.csv` e os 6 `revisor_*.csv` em `data/annotation/`. Rodar de novo só produz resultado diferente se `data/raw/articles_202608191040.csv`, `config/entities.yaml` ou `config/settings.yaml` (threshold do fuzzy match) mudarem — a divisão em si (`SEED = 42`) é estável.

## Pendências / decisões que ainda não são minhas

- **Quem é cada `revisor_N`**: os nomes são genéricos de propósito — combinar com a Bea/equipe quem é quem antes de distribuir os arquivos.
- **Proporção 70/15/15 e sobreposição de 10%** são a minha recomendação para este volume, não uma regra fixa — ajustável em `SPLIT_RATIOS`/`OVERLAP_FRACTION` no topo do script se a equipe quiser outra coisa.
- **Quando avançar pra rodada 2** (subir `ROUND1_ARTICLE_COUNT`) é decisão da equipe — depende de quanto a rodada 1 revelar sobre tempo real gasto por revisor e qualidade/concordância obtida.
- Se o volume crescer com novos CSVs (mais artigos coletados), decidir se completa os splits existentes proporcionalmente ou gera lotes novos — não decidido ainda.
