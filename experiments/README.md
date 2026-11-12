# Experimentos do artigo

Este diretório reúne ferramentas e dados de anotação para avaliar reconhecimento de menções e classificação de teor. O fluxo depende do protocolo em [`docs/protocolo-anotacao-v2.md`](../docs/protocolo-anotacao-v2.md) e das decisões metodológicas em [`docs/decisoes-experimentos.md`](../docs/decisoes-experimentos.md).

Para retomar o trabalho em outra sessão ou com outro modelo, consulte [`docs/CONTINUIDADE-EXPERIMENTOS.md`](../docs/CONTINUIDADE-EXPERIMENTOS.md).

## Estado

As ferramentas de validação, métricas, menções, concordância, léxico e TF-IDF estão implementadas e verificadas. A suíte isolada registrou 77 testes aprovados; o TF-IDF foi exercitado apenas com dados sintéticos e predição simulada, sem treino real no gabarito final. Bea decidiu manter as 60 notícias atuais como estão, usando o CSV de 19/08/2026. A proposta `data/preparacao-revisada-20261112/` tem 60 IDs únicos e 90 atribuições; está PROVISÓRIA e `scope_review` permanece PENDENTE como registro do gerador. As limitações foram aceitas para esta rodada: sete notícias fora do escopo, ID `306` ambíguo, cinco casos de foco misto (`408, 435, 406, 418, 457`), além de alertas em `178, 139, 57, 501, 220`. O ID `373` é sobre PT, que está no escopo. Não substituir candidatas nem corrigir textos nesta rodada. Veja [`data/preparacao/data-quality-improvements.md`](data/preparacao/data-quality-improvements.md) para recomendações futuras. A anotação ainda não começou; não há gabarito consensual nem split final.

Regra vigente: referências genéricas como “o partido”, “a legenda” e “a sigla” não são menções a PT nem a qualquer outro partido e não geram teor para partido (D15). Os demais critérios de D10 para nome completo, palavra comum precedida por “partido”, sigla com artigo/contração e sigla entre parênteses continuam valendo. Não altere as respostas originais para fazer o gabarito coincidir com uma heurística: em particular, `145-6` permanece ligado a Flávio nas planilhas, conforme a decisão de Bea.

## Ambiente de execução

Use `.venv-experiments`, separado do ambiente principal. A instalação foi validada com Python 3.14.3, scikit-learn 1.9.1, NumPy 2.5.4 e SciPy 1.18.1; `pip check` não reportou conflitos. Se o ambiente ainda não existir, crie-o e instale o lock completo:

```sh
python3 -m venv .venv-experiments
.venv-experiments/bin/python -m pip install -r experiments/requirements-ml.lock.txt
```

O ambiente `.venv` principal e os requisitos de produção permanecem sem as dependências de ML. O módulo do léxico de produção continua disponível sem importar scikit-learn; o TF-IDF carrega essa dependência somente no treino.

## Ferramentas disponíveis

Para executar a suíte das ferramentas:

```sh
.venv-experiments/bin/python -m pytest tests/experiments -q
```

Resultado da última verificação, em 10/11/2026: 77 testes passaram em 1,11 s. Também foram verificados `--help` da CLI e um fluxo sintético de ajuste e predição com o mock do classificador; não houve treino real no gabarito final.

`gold.py` gera planilhas, valida anotações e divide artigos por notícia. O texto e os offsets são reconstruídos do CSV bruto usando a segmentação de produção.

```sh
.venv-experiments/bin/python experiments/gold.py --help
.venv-experiments/bin/python experiments/gold.py gerar --artigos experiments/data/piloto_ids.txt --anotadores Bea --saida /tmp/prisma-planilhas
.venv-experiments/bin/python experiments/gold.py validar /caminho/para/anotacao.xlsx --saida /tmp/prisma-gold
.venv-experiments/bin/python experiments/gold.py split --artigos /caminho/para/article_ids.txt --compartilhados /caminho/para/compartilhados.txt --saida /tmp/prisma-split.json
```

`metrics.py` contém as funções de métricas. `mentions.py` expõe `load_entities_snapshot` e `predict_mentions`, sem CLI. Os registros de entrada têm `article_id`, `paragraph_index` e `text`; `include_party_descriptions` é obrigatório e deve ser `False` segundo D15. A saída inclui as menções previstas e `teor` vazio. `teor.py` oferece `predict_lexicon` para classificar pares previstos (`pairs="mentions"`) ou pares presentes nas chaves de teor anotadas (`pairs="gold"`).

`teor.py` também contém `fit_tfidf`, `predict_tfidf` e `mark_target`. O TF-IDF marca spans do alvo como `[ALVO]`; sem span, mantém o texto original e contabiliza o par como não marcado. O treino usa apenas registros com `split=train`, exclui `indeterminado` e ajusta `TfidfVectorizer` com regressão logística balanceada (semente 42, máximo de 1.000 iterações). `pairs="gold"` e `pairs="mentions"` escolhem as chaves que serão classificadas. Essa saída técnica não altera a anotação humana.

A CLI treina e exporta predições em JSONL e metadados em JSON. Use saídas novas, distintas entre si e das entradas; a CLI se recusa a sobrescrever arquivos existentes. O arquivo de predição abaixo é apenas um exemplo de caminho: não existe gabarito final para avaliação ainda.

```sh
.venv-experiments/bin/python experiments/tfidf.py --help
.venv-experiments/bin/python experiments/tfidf.py --train /caminho/para/treino.jsonl --predict /caminho/para/predicoes.jsonl --pairs mentions --saida /tmp/tfidf-predicoes.jsonl --metadata /tmp/tfidf-metadados.json
.venv-experiments/bin/python experiments/agreement.py --help
```

O relatório de concordância usa arquivos preservados nos backups. Os originais disponíveis de cinco anotadores estão em `experiments/data/anotacao/backups/20261102T180337Z`; a planilha disponível de Anderson está em `20261102T183539Z` e já havia recebido edição manual. Portanto, a independência dessa resposta não pode ser confirmada. O backup mais recente inclui alterações de protocolo. A concordância resultante é descritiva, não representa seis respostas comprovadamente independentes nem constitui gabarito consensual. As planilhas de origem não devem ser sobrescritas.

O relatório reproduzível está em `results/piloto-concordancia/`. Com os cinco arquivos do backup `180337Z`, Fleiss é 0,6841 em 55 de 100 unidades completas; o F1 médio entre pares é 0,7303 exato e 0,8196 parcial. O relatório registra hashes e exclusões. Confira `report.md` e `report.json` antes de usar esses valores.

## Dados e próximos passos

- `data/piloto_ids.txt`: IDs das cinco notícias do piloto; o piloto deve permanecer fora da amostra definitiva.
- `data/anotacao/backups/`: cópias preservadas das planilhas e registros de alteração.
- `data/preparacao/`: a proposta mantida para anotação está em [`data/preparacao-revisada-20261112/`](data/preparacao-revisada-20261112/). Gere seis planilhas, uma por anotador, usando os 15 IDs de cada `ids_<nome>.txt`, `gold.py gerar`, o CSV de 19/08 e `data/entidades.yaml`. As limitações aceitas estão em [`data/preparacao/data-quality-improvements.md`](data/preparacao/data-quality-improvements.md).

Para regenerar os artefatos e executar os testes de preparação:

```sh
.venv/bin/python experiments/prepare_data.py
.venv/bin/python -m pytest -q tests/experiments/test_prepare_data.py
```

Registro histórico da geração da amostra: quatro testes passaram; foram atribuídas seis notícias compartilhadas e nove exclusivas por anotador. A seleção e `metadata.json` foram produzidos com o snapshot de 29/08/2026, que continha 40 entidades e 73 aliases. Esse histórico permanece nos arquivos originais e não representa a revalidação atual. O snapshot atualizado está em `data/entidades.yaml`; sua origem, modo somente leitura e hash estão registrados em `data/preparacao/snapshot-audit.json`. A comparação separada com esse snapshot está em `candidate-revalidation.json`.

Bea decidiu manter a amostra atual como está: não substituir candidatas nem corrigir textos nesta rodada. A proposta está PROVISÓRIA, tem 60 IDs únicos e 90 atribuições; `scope_review` permanece PENDENTE como registro do gerador. A próxima etapa é gerar seis planilhas por anotador, uma para cada arquivo `ids_<nome>.txt` com 15 IDs, usando `experiments/gold.py gerar`, `data/raw/articles_202608191040.csv` e `data/entidades.yaml` (snapshot de prisma-dev). A anotação ainda não começou. As limitações aceitas e recomendações futuras estão em [`data/preparacao/data-quality-improvements.md`](data/preparacao/data-quality-improvements.md). Depois: validar anotações independentes, adjudicar as seis compartilhadas, consolidar o gold e gerar o split 39/6/15 com hashes. Não chamar o gabarito ou split de final. Quando uma etapa exigir execução de código, Bea executará o comando fornecido e trará a saída para conferência.
