from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

# Único schema_version aceito nesta versão do Classifier. Payloads com outro
# valor são rejeitados (nack sem requeue -- ver src/message_handler.py) em
# vez de tentar interpretar um contrato desconhecido às cegas.
SUPPORTED_SCHEMA_VERSION = 1

# Campo novo, ainda não presente no contrato real do scrapping-prisma (ver
# src/jobs_publisher.py::build_payload lá) -- todo conteúdo publicado hoje é
# notícia, então assumimos esse default até o payload ganhar um campo
# explícito. Ver README, seção "Limitações conhecidas".
DEFAULT_CONTENT_TYPE = "news_article"

_REQUIRED_STRING_FIELDS = ("title", "full_text", "canonical_link")


class PayloadValidationError(Exception):
    """Payload da fila em formato inválido ou com schema_version não
    suportado. Consumidor mapeia isso para nack sem requeue (ver
    classifier-prisma/context.md, seção 2 e 9) -- não há retry próprio
    aqui, a mensagem vai para a DLQ que o DevOps já configurou."""


@dataclass(frozen=True)
class ScraperPayload:
    schema_version: int
    job_id: int
    article_id: int
    pipeline_version: str
    content_type: str
    title: str
    full_text: str
    canonical_link: str
    source_name: str | None
    published_at: datetime | None


def parse_payload(raw_body: bytes) -> ScraperPayload:
    try:
        raw = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PayloadValidationError(f"corpo da mensagem não é JSON válido: {exc}") from exc

    if not isinstance(raw, dict):
        raise PayloadValidationError("corpo da mensagem deve ser um objeto JSON")

    schema_version = raw.get("schema_version")
    if schema_version != SUPPORTED_SCHEMA_VERSION:
        raise PayloadValidationError(
            f"schema_version {schema_version!r} não suportado (esperado {SUPPORTED_SCHEMA_VERSION})"
        )

    for field in ("job_id", "article_id"):
        if not isinstance(raw.get(field), int):
            raise PayloadValidationError(f"campo '{field}' ausente ou não é inteiro")

    pipeline_version = raw.get("pipeline_version")
    if not isinstance(pipeline_version, str) or not pipeline_version.strip():
        raise PayloadValidationError("campo 'pipeline_version' ausente ou vazio")

    for field in _REQUIRED_STRING_FIELDS:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise PayloadValidationError(f"campo '{field}' ausente ou vazio")

    source_name = raw.get("source_name")
    if source_name is not None and not isinstance(source_name, str):
        raise PayloadValidationError("campo 'source_name' deve ser texto ou nulo")

    published_at_raw = raw.get("published_at")
    published_at: datetime | None = None
    if published_at_raw is not None:
        if not isinstance(published_at_raw, str):
            raise PayloadValidationError("campo 'published_at' deve ser texto (ISO 8601) ou nulo")
        try:
            published_at = datetime.fromisoformat(published_at_raw)
        except ValueError as exc:
            raise PayloadValidationError(f"campo 'published_at' não é uma data ISO 8601 válida: {exc}") from exc

    content_type = raw.get("content_type", DEFAULT_CONTENT_TYPE)
    if not isinstance(content_type, str) or not content_type.strip():
        raise PayloadValidationError("campo 'content_type' deve ser texto não vazio quando presente")

    return ScraperPayload(
        schema_version=schema_version,
        job_id=raw["job_id"],
        article_id=raw["article_id"],
        pipeline_version=pipeline_version.strip(),
        content_type=content_type.strip(),
        title=raw["title"],
        full_text=raw["full_text"],
        canonical_link=raw["canonical_link"],
        source_name=source_name,
        published_at=published_at,
    )
