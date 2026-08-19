# RabbitMQ — ambiente de desenvolvimento local

Estes arquivos (`definitions.json`, `rabbitmq.conf`) existem **só para o
`docker-compose.yml` deste repositório** conseguir subir um RabbitMQ local
já com a topologia (filas, exchange, bind) que o `classifier-prisma`
precisa para testar o consumidor e o publisher ponta a ponta.

## Isso NÃO é a configuração de produção

Em produção, o Classifier **nunca declara topologia nenhuma** — nem fila,
nem exchange, nem bind (ver `src/rabbitmq_consumer.py` e
`src/rabbitmq_publisher.py`, ambos documentam isso explicitamente). Quem
cria e mantém a topologia real no broker de produção é o time de DevOps,
fora deste repositório.

- `scraper.dados.queue` (fronteira Scraper → Classifier): nome **já
  confirmado** pelo DevOps. Reproduzido aqui fielmente.
- `classifier.saida.exchange` e `classifier.notificacoes.queue` (fronteira
  Classifier → Analyser): nomes **ainda não confirmados oficialmente** —
  são os placeholders usados no `.env.example` para permitir desenvolver o
  padrão outbox agora. Se o DevOps definir nomes diferentes em produção,
  atualize `.env`/`.env.example` e `definitions.json` juntos, mas não
  assuma que este arquivo é a fonte da verdade de produção.

## Como funciona

`rabbitmq.conf` usa a diretiva `load_definitions` (nativa do RabbitMQ) para
carregar `definitions.json` no boot do broker. É a forma mais simples e
declarativa de provisionar filas/exchange/bind sem escrever script de init
ou container adicional — o próprio RabbitMQ lê o JSON e cria tudo antes de
aceitar conexões.

`rabbitmq.conf` também define `loopback_users = none`. Isso é necessário
porque, com o Docker, a conexão do host (`localhost:5672`) para o container
passa pelo NAT do Docker e não é vista pelo RabbitMQ como uma conexão de
loopback de verdade — sem essa configuração, o usuário `guest` (que só pode
logar via loopback por padrão) seria bloqueado. **Isso é aceitável só em
desenvolvimento local — nunca fazer isso em produção.**
