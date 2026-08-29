"""Exporta a lista de entidades em escopo (v1: presidente + vice-presidente
e os partidos vinculados a eles) de `entidade_matching`/`tabela_candidatura`
(`app_db2`) para um arquivo YAML.

**Este arquivo NÃO é consumido por nada dentro do classifier-prisma.** Ele
existe para ser copiado manualmente para o repositório `scrapping-prisma`
(`config/target_entities.yaml` de lá) -- ver
`docs/propostas-mudancas-scraper.md`, seção 2, para o raciocínio completo:
o scraper precisa saber quem procurar/filtrar na coleta de notícias, mas
não pode ler `app_db2` diretamente (regra do projeto: nenhum componente lê
banco de outro componente -- ver ADR 0001).

Cadência: geração SOB DEMANDA, não agendada. A lista de candidatos/partidos
muda por evento raro do TSE (troca de vice, nova coligação, impugnação),
não por tempo -- não há motivo pra rodar isso num cron. Rode de novo (e
copie o arquivo pro scrapping-prisma) sempre que uma candidatura do escopo
v1 mudar, ou quando alguém desconfiar que a lista pode estar desatualizada.

Reaproveita `entities_store.load_active_entities_from_db` (mesma função
usada em produção por `src/main.py`, mesmo filtro v1 de
`config/settings.yaml`, `entity_scope.cargos`) -- não duplica a lógica de
quais entidades entram no escopo. A única coisa que falta nessa função para
o export é o `ds_cargo` de cada político (`Entity` não carrega esse campo,
porque `src/entity_matcher.py` nunca precisou dele em produção). Em vez de
alterar `load_active_entities_from_db` -- e arriscar mudar comportamento
usado em produção -- este script faz uma SEGUNDA query, pequena e
independente, só para buscar `ds_cargo` pelos ids de político já
carregados (mesmo espírito de `load_real_entities_full_scope` em
scripts/benchmark_entity_matcher.py: leitura extra isolada no script,
sem tocar em `src/entities_store.py`).

Só faz SELECT (nunca INSERT/UPDATE/DELETE) em `app_db2`.

Uso:
    .venv/bin/python scripts/export_target_entities.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import entities_store  # noqa: E402  (precisa do sys.path acima)
import storage  # noqa: E402
from entities_store import Entity  # noqa: E402

SETTINGS_PATH = BASE_DIR / "config" / "settings.yaml"
ENV_PATH = BASE_DIR / ".env"
OUTPUT_PATH = BASE_DIR / "data" / "exports" / "target_entities.yaml"

SCHEMA_VERSION = 1
SOURCE_LABEL = (
    "classifier-prisma/entidade_matching (TSE 2026, escopo v1: presidente + vice-presidente)"
)


def load_cargos_from_settings() -> list[str]:
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    return settings["entity_scope"]["cargos"]


def load_cargo_by_titulo(conn: psycopg.Connection, titulos: list[str]) -> dict[str, str]:
    """Busca `ds_cargo` para os políticos já carregados por
    `load_active_entities_from_db`, pelo id (`nr_titulo_eleitoral_candidato`).
    Query separada e pequena de propósito -- ver docstring do módulo. Só
    SELECT."""
    if not titulos:
        return {}

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT nr_titulo_eleitoral_candidato, ds_cargo
            FROM entidade_matching
            WHERE entity_type = 'politico'
              AND nr_titulo_eleitoral_candidato = ANY(%(titulos)s)
            """,
            {"titulos": titulos},
        )
        rows = cur.fetchall()

    return {str(titulo): cargo for titulo, cargo in rows}


def build_export_entities(
    entities: list[Entity], cargo_by_titulo: dict[str, str]
) -> list[dict]:
    """Monta a lista no formato do YAML de export -- ver docstring do
    módulo para o formato exato. `cargo` é `None` (vira `null` no YAML)
    para partido, obrigatório para político."""
    export_entities: list[dict] = []
    for entity in entities:
        if entity.entity_type == "politico":
            cargo = cargo_by_titulo.get(entity.id)
            if not cargo:
                raise ValueError(
                    f"político {entity.canonical_name!r} (id={entity.id!r}) sem "
                    "ds_cargo encontrado -- inconsistência entre "
                    "load_active_entities_from_db e a query de cargo deste script"
                )
        else:
            cargo = None

        export_entities.append(
            {
                "entity_id": entity.id,
                "entity_type": entity.entity_type,
                "cargo": cargo,
                "nome_principal": entity.canonical_name,
                "aliases": list(entity.aliases),
            }
        )

    # Ordem determinística (não depende da ordem do UNION ALL do SQL) --
    # facilita diff do arquivo gerado entre execuções.
    export_entities.sort(key=lambda e: (e["entity_type"], e["nome_principal"]))
    return export_entities


def build_export_document(entities: list[Entity], cargo_by_titulo: dict[str, str]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "schema_version": SCHEMA_VERSION,
        "source": SOURCE_LABEL,
        "entities": build_export_entities(entities, cargo_by_titulo),
    }


def write_yaml(document: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Gerado por scripts/export_target_entities.py -- NÃO editar manualmente.\n"
        "# Este arquivo é para ser COPIADO MANUALMENTE para o repositório\n"
        "# scrapping-prisma (config/target_entities.yaml de lá). Não é\n"
        "# consumido por nada dentro do classifier-prisma. Ver\n"
        "# docs/propostas-mudancas-scraper.md, seção 2.\n"
        "# Cadência: geração sob demanda (candidatura muda por evento raro\n"
        "# do TSE, não por tempo) -- não agendar isto em cron.\n"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(
            document,
            f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )


def main() -> int:
    load_dotenv(ENV_PATH)
    cargos = load_cargos_from_settings()

    conn = storage.get_connection()
    try:
        entities = entities_store.load_active_entities_from_db(conn, cargos)
        titulos = [e.id for e in entities if e.entity_type == "politico"]
        cargo_by_titulo = load_cargo_by_titulo(conn, titulos)
    finally:
        conn.rollback()  # defensivo -- só fizemos SELECT
        conn.close()

    document = build_export_document(entities, cargo_by_titulo)
    write_yaml(document, OUTPUT_PATH)

    n_politicos = sum(1 for e in document["entities"] if e["entity_type"] == "politico")
    n_partidos = sum(1 for e in document["entities"] if e["entity_type"] == "partido")
    print(f"Exportado: {n_politicos} políticos + {n_partidos} partidos = "
          f"{len(document['entities'])} entidades -> {OUTPUT_PATH}")
    print("Lembre-se de copiar este arquivo manualmente para "
          "scrapping-prisma/config/target_entities.yaml.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
