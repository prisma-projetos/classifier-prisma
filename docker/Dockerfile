FROM python:3.14-slim AS base

# Dependências de sistema mínimas para compilar pacotes sem wheel pronto
# para esta plataforma (ex.: rapidfuzz em algumas arquiteturas). psycopg
# aqui é o [binary], que já traz wheel pré-compilado, mas manter build
# tooling não custa e evita builds quebrados em arquiteturas menos comuns.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instala dependências primeiro para aproveitar cache de camada do Docker
# quando só o código-fonte muda.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código e configuração versionada necessários em runtime (main.py resolve
# caminhos de config/ e logs/ a partir da raiz do projeto — ver
# BASE_DIR em src/main.py).
COPY src/ ./src/
COPY config/ ./config/

# logs/ não é versionado (ver .gitignore) mas src/main.py cria o diretório
# automaticamente via log_file.parent.mkdir(parents=True, exist_ok=True).

# Usuário não-root
RUN useradd --create-home --shell /bin/bash classifier \
    && chown -R classifier:classifier /app
USER classifier

# src/ não é um pacote instalável (imports são planos, ex. "import storage"
# dentro de src/main.py) — WORKDIR /app/src garante que esses imports
# funcionem sem precisar mexer no PYTHONPATH.
WORKDIR /app/src

# Processo de produção padrão: o daemon contínuo que consome a fila do
# Scraper (ver README, seção "Modo de execução"). Para rodar
# notify_analyser.py, sobrescreva o comando (ver exemplo acima).
CMD ["python", "main.py"]