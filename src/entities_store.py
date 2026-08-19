from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

VALID_ENTITY_TYPES = {"politician", "party"}


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
