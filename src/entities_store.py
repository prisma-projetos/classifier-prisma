from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

VALID_ENTITY_TYPES = {"politician", "party"}

# Valores reais da coluna `entidade_matching.entity_type` (banco compartilhado
# `app_db2`) -- diferentes dos valores usados pelo YAML legado acima
# (`politician`/`party`). Ver `load_active_entities_from_db`.
DB_ENTITY_TYPES = {"politico", "partido"}


@dataclass(frozen=True)
class Entity:
    id: str
    canonical_name: str
    entity_type: str
    aliases: tuple[str, ...]

    def all_match_candidates(self) -> tuple[str, ...]:
        """Candidatos usados no casamento de entidade (src/entity_matcher.py)
        -- o nome canônico conta como mais um apelido, sem precisar ser
        repetido manualmente em `aliases` no YAML."""
        return (self.canonical_name, *self.aliases)


@dataclass(frozen=True)
class SyncResult:
    inserted: int
    updated: int


class EntityValidationError(Exception):
    """Agrega todos os problemas encontrados no YAML, não só o primeiro."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def _validate_entry(index: int, raw: dict) -> tuple[Entity | None, list[str]]:
    errors: list[str] = []
    label = f"entidade #{index}"

    raw_id = raw.get("id")
    entity_id = None
    if not isinstance(raw_id, str) or not raw_id.strip():
        errors.append(f"{label}: campo 'id' ausente ou vazio")
    else:
        entity_id = raw_id.strip()
        label = f"entidade '{entity_id}'"

    raw_name = raw.get("canonical_name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        errors.append(f"{label}: campo 'canonical_name' ausente ou vazio")

    entity_type = raw.get("entity_type")
    if entity_type not in VALID_ENTITY_TYPES:
        errors.append(
            f"{label}: campo 'entity_type' com valor inválido {entity_type!r} "
            f"(esperado um de {sorted(VALID_ENTITY_TYPES)})"
        )

    raw_aliases = raw.get("aliases", [])
    aliases: list[str] = []
    if not isinstance(raw_aliases, list):
        errors.append(f"{label}: campo 'aliases' deve ser uma lista")
    else:
        for alias in raw_aliases:
            if not isinstance(alias, str) or not alias.strip():
                errors.append(f"{label}: 'aliases' contém um item inválido ({alias!r})")
                continue
            aliases.append(alias.strip())

    if errors:
        return None, errors

    return (
        Entity(
            id=entity_id,
            canonical_name=raw_name.strip(),
            entity_type=entity_type,
            aliases=tuple(aliases),
        ),
        [],
    )


def load_and_validate(path: Path) -> list[Entity]:
    with open(path, encoding="utf-8") as f:
        raw_doc = yaml.safe_load(f)

    if not isinstance(raw_doc, dict) or "entities" not in raw_doc:
        raise EntityValidationError([f"{path}: chave raiz 'entities' ausente"])

    raw_entities = raw_doc["entities"]
    if not isinstance(raw_entities, list):
        raise EntityValidationError([f"{path}: 'entities' deve ser uma lista"])

    entities: list[Entity] = []
    all_errors: list[str] = []
    seen_ids: dict[str, int] = {}

    for index, raw in enumerate(raw_entities, start=1):
        if not isinstance(raw, dict):
            all_errors.append(f"entidade #{index}: entrada deve ser um mapeamento de campos")
            continue

        entity, errors = _validate_entry(index, raw)
        if errors:
            all_errors.extend(errors)
            continue

        if entity.id in seen_ids:
            all_errors.append(f"entidade '{entity.id}' (#{index}): id duplicado, já usado na entidade #{seen_ids[entity.id]}")
            continue

        seen_ids[entity.id] = index
        entities.append(entity)

    if all_errors:
        raise EntityValidationError(all_errors)

    return entities


def sync_entities(conn: psycopg.Connection, entities: list[Entity]) -> SyncResult:
    """Upsert idempotente em `political_entities`. Nunca apaga entidades
    ausentes do YAML -- só não as toca. Não comita a transação: quem chama
    decide quando (mesmo padrão de src/search_terms_store.py do
    scrapping-prisma)."""
    inserted = 0
    updated = 0

    with conn.cursor() as cur:
        for entity in entities:
            cur.execute(
                """
                INSERT INTO political_entities (id, canonical_name, entity_type, aliases)
                VALUES (%(id)s, %(canonical_name)s, %(entity_type)s, %(aliases)s)
                ON CONFLICT (id) DO UPDATE SET
                    canonical_name = EXCLUDED.canonical_name,
                    entity_type = EXCLUDED.entity_type,
                    aliases = EXCLUDED.aliases,
                    updated_at = now()
                RETURNING (xmax = 0) AS was_insert
                """,
                {
                    "id": entity.id,
                    "canonical_name": entity.canonical_name,
                    "entity_type": entity.entity_type,
                    "aliases": json.dumps(list(entity.aliases)),
                },
            )
            row = cur.fetchone()
            if row[0]:
                inserted += 1
            else:
                updated += 1

    return SyncResult(inserted=inserted, updated=updated)


def load_active_entities_from_db(
    conn: psycopg.Connection, cargos: Sequence[str]
) -> list[Entity]:
    """Carrega entidades ativas de `entidade_matching` (System DB
    compartilhado `app_db2` -- ver classifier-prisma/context.md) para uso em
    `src/entity_matcher.py`. É a fonte usada em produção a partir desta
    migração (chamada por `src/main.py`), no lugar de `load_and_validate()`
    (YAML) -- `entidade_matching` é gerenciada por outro processo (ingestão
    do TSE), então esta função só LÊ, nunca escreve (não existe
    equivalente a `sync_entities` para ela).

    `Entity.id` vira a chave real da entidade -- `nr_titulo_eleitoral_candidato`
    quando `entity_type='politico'`, `nr_partido` quando `entity_type='partido'`
    -- sempre como string, mesmo formato usado depois em
    `EntityMatch.entity_id` (src/entity_matcher.py) e na gravação em
    `tabela_classificacao` (src/classification_store.py). `Entity.entity_type`
    usa os valores reais da coluna (`'politico'`/`'partido'`, ver
    `DB_ENTITY_TYPES`), diferente dos valores do YAML legado
    (`'politician'`/`'party'`, ver `VALID_ENTITY_TYPES`).

    `cargos`: recorte de fase da v1 (decisão da equipe, 2026-08-28 -- ver
    data/annotation/pendencias-tecnicas.md) -- NÃO é limitação técnica.
    Filtra políticos cujo `ds_cargo` esteja nessa lista (ex.:
    `['PRESIDENTE', 'VICE-PRESIDENTE']`, configurável em
    `config/settings.yaml`, seção `entity_scope.cargos`) e só os partidos
    (`entity_type='partido'`) associados a esses candidatos. `ds_cargo` só é
    populado em `entidade_matching` para `entity_type='politico'` -- não dá
    pra filtrar partido direto por essa coluna. O vínculo candidato->partido
    não existe dentro de `entidade_matching` (`nr_partido` só é preenchido
    para `entity_type='partido'`) -- por isso o JOIN com
    `tabela_candidatura` (mesmo schema compartilhado), usando
    `nr_titulo_eleitoral_candidato` como chave. Cast explícito
    (`nr_partido::integer`) porque `tabela_candidatura.nr_partido` é
    `integer`. O `UNION ALL` final também exige `em.nr_partido::text` no
    ramo de partido -- descoberto em produção (2026-08-30): o tipo real de
    `entidade_matching.nr_partido` variou entre ambientes (`text` no banco
    de dev usado nesta migração, `integer` no ambiente onde o Anderson
    testou) -- sem esse cast explícito, o UNION falha com
    `DatatypeMismatch` sempre que os dois ramos não combinam exatamente no
    mesmo tipo. O cast deixa a query correta nos dois casos.

    NÃO filtra nem altera a coluna `ativo` de `entidade_matching` além do
    `WHERE ativo = true` que já existia -- esse campo é compartilhado com
    outros componentes da plataforma (ver pendencias-tecnicas.md); o recorte
    de fase é só nesta query de leitura.

    Levanta `ValueError` se encontrar uma linha com `entity_type` fora do
    esperado ou sem a chave correspondente preenchida -- falha alta e
    explícita, em vez de deixar essa entidade quebrar o matching
    silenciosamente mais adiante."""
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH presidenciaveis AS (
                SELECT em.nr_titulo_eleitoral_candidato,
                       em.entity_type,
                       em.nome_principal,
                       em.aliases,
                       tc.nr_partido AS nr_partido_candidatura
                FROM entidade_matching em
                JOIN tabela_candidatura tc
                  ON tc.nr_titulo_eleitoral_candidato = em.nr_titulo_eleitoral_candidato
                WHERE em.entity_type = 'politico'
                  AND em.ativo = true
                  AND em.ds_cargo = ANY(%(cargos)s)
            )
            SELECT entity_type, nr_titulo_eleitoral_candidato, NULL::text AS nr_partido,
                   nome_principal, aliases
            FROM presidenciaveis

            UNION ALL

            SELECT em.entity_type, NULL::text AS nr_titulo_eleitoral_candidato,
                   em.nr_partido::text AS nr_partido, em.nome_principal, em.aliases
            FROM entidade_matching em
            WHERE em.entity_type = 'partido'
              AND em.ativo = true
              AND em.nr_partido::integer IN (
                  SELECT DISTINCT nr_partido_candidatura FROM presidenciaveis
              )
            """,
            {"cargos": list(cargos)},
        )
        rows = cur.fetchall()

    entities: list[Entity] = []
    for entity_type, nr_titulo, nr_partido, nome_principal, aliases in rows:
        if entity_type not in DB_ENTITY_TYPES:
            raise ValueError(
                f"entidade_matching: entity_type {entity_type!r} inesperado para "
                f"{nome_principal!r} (esperado um de {sorted(DB_ENTITY_TYPES)})"
            )

        entity_id = nr_titulo if entity_type == "politico" else nr_partido
        if not entity_id:
            raise ValueError(
                f"entidade_matching: entidade {nome_principal!r} (entity_type={entity_type!r}) "
                "sem chave preenchida (nr_titulo_eleitoral_candidato/nr_partido nulo ou vazio)"
            )

        entities.append(
            Entity(
                id=str(entity_id),
                canonical_name=nome_principal,
                entity_type=entity_type,
                aliases=tuple(aliases or []),
            )
        )

    return entities
