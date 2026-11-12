# **PRISMA: pergunta de pesquisa e plano de testes**

O artigo junta duas frentes: reconhecer as menções diretas e indiretas a cada candidato (REM) e comparar formas de classificar o teor do que se diz sobre ele, em notícias políticas em português. É o artigo obrigatório do projeto, sem publicação, com entrega em 19/11/2026.  
Os termos técnicos estão explicados no Glossário, e os temas para buscar referências estão na seção Assuntos para pesquisar, as duas no fim do documento.

## **Pergunta de pesquisa**

**Em notícias políticas em português, reconhecer também as menções indiretas aos candidatos e usar classificadores treinados melhora a análise do teor sobre cada candidato, em comparação com o sistema atual, que acha só o nome e classifica por léxico?**  
Perguntas menores:

> 1. Que parcela das menções a candidatos é indireta, e qual método as reconhece melhor?  
> 2. Qual forma de classificar o teor (positivo, negativo, neutro) funciona melhor?  
> 3. Quanto o resultado por candidato muda quando as duas melhorias são combinadas?

**Hipótese:** os modelos treinados superam as abordagens simples nas duas etapas, sobretudo nas menções descritivas ("o presidente", "o candidato") e nos parágrafos que citam mais de um candidato.  
**Definições:**

> * **Menção direta:** nome, apelido ou sigla.  
> * **Menção indireta:** pronome ("ele", "ela") ou descrição ("o presidente", "o candidato", "o petista").  
> * **Teor:** se o trecho fala bem (positivo), mal (negativo) ou de forma neutra (neutro) de um candidato específico. "Economistas elogiaram a reforma de Lula" é positivo para o Lula; "Lula foi criticado pela oposição" é negativo; "Lula participou do evento" é neutro. Um mesmo parágrafo pode ser positivo para um candidato e negativo para outro.  
> * **REM (reconhecimento de menções):** achar cada trecho que menciona um candidato e dizer a qual candidato ele se refere. A tarefa inclui as duas coisas: detectar e ligar.  
> * **Escopo:** candidatos a presidente e vice e seus partidos, o mesmo recorte do Classifier hoje (40 entidades).

## **Abordagens simples**

São as linhas de base (baselines): regras escritas à mão, que não aprendem com dados anotados. O modelo treinado só se justifica se for melhor que elas.  
**Para reconhecer as menções:**

> * **Matcher por nome** (entity\_matcher.py, em produção): procura o nome ou apelido do candidato no parágrafo, de forma exata ou aproximada. Não acha nenhuma menção indireta.  
> * **Menção mais recente** (descrita em docs/pesquisa-correferencia.md, ainda não implementada): atribui "o presidente", "o candidato" e "ele" à última pessoa citada pelo nome na notícia. Erra quando a notícia alterna entre dois candidatos.

**Para classificar o teor:**

> * **Léxico** (lexicon.py, em produção): conta palavras positivas e negativas de uma lista feita à mão e escolhe o lado com mais ocorrências. Não trata negação, ironia nem citação, e dá a mesma classe a todos os candidatos do parágrafo.

O sistema atual é a combinação matcher por nome \+ léxico, e é o piso de todo o experimento.

## **Famílias comparadas**

Hoje o código do Classifier só tem regras: não há nenhuma biblioteca de aprendizado de máquina no requirements.txt. Todo o lado treinado será escrito numa pasta separada (experiments/), sem mexer na produção.

| Família | Reconhecer menções | Classificar o teor | Onde roda |
| :---- | :---- | :---- | :---- |
| Regras | Matcher por nome; menção mais recente | Léxico | VPS, segundos |
| Aprendizado tradicional | — | TF-IDF \+ regressão logística | VPS, segundos |
| Transformer ajustado | BERTimbau marcando os trechos | BERTimbau com o candidato marcado | VPS, em CPU: treino lento, uso rápido |

**Ficam como trabalho futuro:** LLM (zero-shot e few-shot), BiLSTM com embeddings do NILC, CRF para menções e a ferramenta CORP (PUCRS).  
**Por que o LLM saiu:** o grupo não tem API de LLM disponível, e o LLM local (Qwen 9B no Ollama) derruba a VPS de 4 CPUs e 16 GB. Por isso ele não será testado. A pergunta de pesquisa não depende dele.

## **O que precisamos testar**

**Testes de cada etapa:**

> * **Menções:** matcher, menção mais recente e BERTimbau. Métricas: precisão, revocação e F1 por trecho (acerto exato e parcial), separadas em diretas e indiretas, e acerto do candidato ligado a cada menção. O primeiro resultado é a parcela de menções indiretas no gabarito.  
> * **Teor:** léxico, TF-IDF e BERTimbau. Métricas: F1 por classe, F1 macro e matriz de confusão, usando as menções do gabarito para isolar esta etapa.  
> * **Fim a fim:** com as menções previstas, F1 macro sobre os pares (candidato, teor) de cada parágrafo, e o saldo de positivos, negativos e neutros por candidato comparado ao do gabarito.  
> * **Custo:** tempo por mil parágrafos na VPS de 4 CPUs e 16 GB.  
> * **Concordância entre anotadores:** kappa de Fleiss para o teor e F1 entre anotadores para os trechos.

O teste central combina as duas etapas em quatro sistemas. Assim dá para ver quanto do ganho vem das menções e quanto vem da classificação.

| Sistema | Menções | Teor | O que mostra | F1 macro fim a fim |
| :---- | :---- | :---- | :---- | :---- |
| A (atual) | Matcher por nome | Léxico | O piso | a preencher |
| B | Matcher por nome | BERTimbau | Ganho só da classificação | a preencher |
| C | BERTimbau (REM) | Léxico | Ganho só das menções | a preencher |
| D (proposto) | BERTimbau (REM) | BERTimbau | O sistema completo | a preencher |

**Por que quatro sistemas:** cruzar um método simples e um treinado em cada etapa é o menor desenho que separa os ganhos. B contra A mostra o ganho só da classificação, C contra A só o das menções, e D mostra os dois juntos. Mais combinações aumentam as tabelas e o tempo de análise sem mudar essa resposta.  
**Por que só léxico e BERTimbau no teor:** são os dois extremos. O léxico é o sistema atual (o piso), e o BERTimbau é o candidato com maior desempenho esperado. O TF-IDF entra nos testes da etapa de teor, para mostrar onde fica entre os dois; o mesmo vale para a menção mais recente na etapa de menções.  
**Se sobrar tempo:** como combinar saídas já prontas é só código, dá para ampliar para 3 × 3 (9 sistemas), incluindo a menção mais recente e o TF-IDF, sem treinar nada de novo.

## **Planejamento dos testes**

**Gabarito.** Tirar a amostra das 321 notícias reservadas, que ainda não foram anotadas, para não misturar com a rodada 1\. Anotar todos os parágrafos de cada notícia, inclusive os sem nome, porque é neles que estão as menções indiretas. Cada parágrafo recebe duas anotações: os trechos que mencionam cada candidato (com o tipo, direta ou indireta) e o teor sobre cada candidato mencionado.  
**Tamanho.** 60 notícias no gabarito. Dessas, 6 (10%) são anotadas por todos, para medir a concordância, e as outras 54 são divididas sem repetição: 9 para cada membro. Antes, todos anotam as mesmas 5 notícias do piloto, que ficam fora do gabarito porque o protocolo ainda vai mudar depois delas.

| Membro | Piloto | Compartilhadas | Próprias | Total |
| :---- | :---- | :---- | :---- | :---- |
| Anderson | 5 | 6 | 9 | 20 |
| Nicolas | 5 | 6 | 9 | 20 |
| Lucas | 5 | 6 | 9 | 20 |
| Cristovam | 5 | 6 | 9 | 20 |
| Gustavo | 5 | 6 | 9 | 20 |
| Beatriz | 5 | 6 | 9 | 20 |

Se cada notícia levar uns 15 minutos (o piloto confirma), são cerca de 5 horas por pessoa entre 27/10 e 31/10. Se demorar mais, reduzam as próprias e mantenham as 6 compartilhadas: com 7 próprias, o gabarito fica com 48 notícias; com 5, fica com 36, o mínimo recomendado. Outros membros do projeto podem anotar também, se fizerem o piloto.  
**Divisão dos dados.** Por notícia, nunca por parágrafo: 70% treino, 15% validação e 15% teste. O teste é congelado em 31/10 e usado só na avaliação final.  
**Dados que já existem.** Os 1.054 pares com rótulo de teor da rodada 1 podem reforçar o treino dos classificadores de teor, mas nunca entram no teste, porque só têm menções diretas.  
**Treino do BERTimbau em CPU:** sem Colab nem Kaggle, os dois BERTimbau serão treinados na própria VPS, sem GPU. O modelo cabe na memória (cerca de 4 a 8 GB, com lotes de 8 a 16 exemplos e textos de até 256 tokens), mas cada rodada de treino deve levar de 1 a 3 horas. Esses números são estimativas: medir antes com uma rodada pequena. Cuidados:

> * treinar de madrugada, para não deixar o Classifier e a API lentos;  
> * limitar as threads do treino (por exemplo, torch.set\_num\_threads(2));  
> * fazer poucas rodadas, com as configurações-padrão, em vez de testar muitas combinações;  
> * se alguém tiver um computador com GPU, usar ele no lugar da VPS.

Depois de treinado, o BERTimbau classifica em CPU em uma fração de segundo por parágrafo, então serve para produção.  
**Passos:**

> 1. Escrever o protocolo de anotação de menções e teor, com exemplos de casos difíceis.  
> 2. Piloto: todos anotam as mesmas 5 notícias, comparam e ajustam o protocolo.  
> 3. Anotar a amostra, com sobreposição de 10%.  
> 4. Resolver as divergências e congelar o teste.  
> 5. Implementar a menção mais recente, o TF-IDF e os dois BERTimbau em experiments/. As duas primeiras podem andar junto com a anotação.  
> 6. Rodar cada etapa e os quatro sistemas no teste uma única vez, registrando a versão de cada modelo.  
> 7. Analisar os erros por tipo de menção e por caso difícil (negação, ironia, citação, notícias com dois candidatos).

## **Cronograma**

Tudo depende de congelar o teste em 31/10: isso libera o treino dos dois BERTimbau. As baselines e o TF-IDF andam junto com a anotação, e os resultados precisam estar fechados em 7/11 para a apresentação de 12/11.

| Etapa | Datas |
| :---- | :---- |
| Pesquisa de referências | 24/10 a 28/10 |
| Protocolo e piloto | 24/10 a 27/10 |
| Anotação da amostra | 27/10 a 31/10 |
| Baselines e TF-IDF | 25/10 a 01/11 |
| **Congelar o teste** | **31/10** |
| Treino BERTimbau (teor e REM) | 01/11 a 05/11 |
| Testes e resultados fechados | 04/11 a 07/11 |
| Escrita dos resultados | 07/11 a 11/11 |
| Apresentação final | 12/11 |
| Revisão e formatação | 13/11 a 18/11 |
| Entrega do artigo | 19/11 |

## **Responsáveis**

Sugestão de divisão, a ajustar pela equipe. Todos anotam a amostra; os outros membros do projeto ajudam nos testes e no artigo.

| Frente | Responsável | Entrega | Prazo |
| :---- | :---- | :---- | :---- |
| Protocolo de anotação e piloto | Cristovam e Nicolas | Protocolo com exemplos e piloto de 5 notícias | 27/10 |
| Extração da amostra e consultas no banco | Gustavo | Amostra exportada e números de motivação (parágrafos sem nome, neutros com confiança 0\) | 26/10 |
| Anotação da amostra | Todos | Menções e teor anotados, com 10% de sobreposição | 31/10 |
| Menção mais recente e pasta experiments/ | Beatriz | Heurística e estrutura dos experimentos, sem mexer na produção | 01/11 |
| TF-IDF e código das métricas | Lucas | Classificador TF-IDF e cálculo de precisão, revocação, F1 e kappa | 01/11 |
| Treino dos dois BERTimbau na VPS | Anderson e Beatriz | Modelos de teor e de REM treinados em CPU | 05/11 |
| Testes dos quatro sistemas | Lucas e Beatriz | Tabelas de resultados | 07/11 |
| Escrita do artigo e apresentação | Nicolas e Cristovam, com todos | Texto dos resultados e apresentação de 12/11 | 11/11 |

## **O que mudou em relação ao documento entregue**

O professor ainda não sabe destas mudanças. Avisem antes de seguir, para não haver surpresa na apresentação de 12/11.

> * **Foco:** antes, comparar cinco famílias de modelos só para classificar o teor. Agora, reconhecer as menções diretas e indiretas a cada candidato e, junto, comparar formas de classificar o teor.  
> * **Correferência:** antes era um problema em aberto, para depois. Agora é o centro do artigo.  
> * **Analyser:** antes era a terceira contribuição. Agora saiu do artigo.  
> * **Modelos:** o BiLSTM e o LLM saíram e ficam como trabalho futuro.  
> * **Gabarito:** antes, pares só com o teor (rodadas 1 e 2). Agora, uma amostra nova anotada duas vezes: os trechos de menção e o teor.  
> * **Datas:** o teste congela em 31/10, e não mais em 23/10. A apresentação (12/11) e a entrega (19/11) continuam.

## **Glossário**

> * **Baseline (abordagem simples):** solução mais fácil e barata, usada como régua para medir se o modelo novo vale a pena.  
> * **Gabarito:** a amostra anotada pelas pessoas, com a resposta certa de cada caso.  
> * **Congelar o teste:** fixar a parte do gabarito usada na avaliação final, que não muda mais e não é usada para treinar.  
> * **Precisão:** do que o método encontrou, quanto estava certo.  
> * **Revocação:** do que estava no gabarito, quanto o método encontrou.  
> * **F1:** média harmônica entre precisão e revocação; resume as duas num número.  
> * **F1 macro:** média do F1 das três classes de teor, com o mesmo peso para cada uma. Impede que o neutro, que é a maioria, esconda os erros em positivo e negativo.  
> * **Matriz de confusão:** tabela que mostra, para cada classe certa, o que o método respondeu.  
> * **Acerto exato e parcial:** no exato, o trecho encontrado precisa ser idêntico ao do gabarito ("o presidente"); no parcial, basta se sobrepor ("presidente").  
> * **Fim a fim:** o sistema completo, das menções até o teor, avaliado de uma vez.  
> * **Kappa de Fleiss:** medida de concordância entre vários anotadores, descontando o acaso.  
> * **TF-IDF:** forma de transformar o texto em números a partir das palavras que aparecem nele.  
> * **BERTimbau:** modelo de linguagem pré-treinado em português, ajustado (fine-tuning) com o gabarito do grupo.

## **Assuntos para pesquisar**

Temas e termos para achar as referências do artigo. Busquem na ACL Anthology, no Google Scholar, na SOL/SBC (STIL, BRACIS, PROPOR), na BDTD e no arXiv.

> * **Lacuna (provar a novidade):** coreference AND political news AND Portuguese; mention detection AND election; target sentiment AND coreference AND news; menções indiretas AND candidatos.  
> * **Correferência em português:** Corref-PT; Summ-it; CORP coreference Portuguese; correferência português. Ponto de partida: [Corref-PT](https://www.cys.cic.ipn.mx/index.php/CyS/article/view/3063).  
> * **Menções a pessoas em notícias:** named person coreference news; quote attribution news. Ponto de partida: [Agarwal et al.](https://arxiv.org/abs/1810.11476).  
> * **Menções descritivas:** definite descriptions coreference; bridging anaphora; role-based mentions politicians.  
> * **Heurística da menção mais recente:** recency heuristic anaphora resolution; Hobbs algorithm; centering theory; rule-based coreference sieve.  
> * **Etiquetagem de trechos com BERT:** Portuguese NER; BERTimbau NER; token classification BIO; span-based mention detection.  
> * **Avaliação de trechos:** span-level F1; strict vs relaxed matching; seqeval; CoNLL-2003 evaluation.  
> * **Sentimento orientado a alvo:** target-dependent sentiment news; NewsMTSC; entity-level sentiment. Ponto de partida: [Rønningstad et al. (2024)](https://arxiv.org/abs/2407.03916).  
> * **Léxico e aprendizado tradicional em português:** lexicon-based sentiment analysis; OpLexicon; SentiLex-PT; TF-IDF logistic regression sentiment Portuguese.  
> * **Transformers para sentimento em português:** BERTimbau sentiment; fine-tuning BERT Portuguese sentiment.  
> * **Anotação e concordância:** annotation guidelines coreference OntoNotes; Krippendorff alpha unitizing; Fleiss kappa.