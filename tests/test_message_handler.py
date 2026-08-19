from __future__ import annotations

import json
import re

import pytest

import classification_store
import entities_store
import message_handler
from entities_store import Entity
from lexicon import Lexicon

# Entidade e léxico mínimos, só o suficiente para o pipeline gerar pelo
# menos uma classificação real (não vazia) -- mesmo padrão inline usado em
# tests/test_entity_matcher.py, sem depender de config/entities.yaml ou
# config/lexicon.yaml reais.
LULA = Entity(id="lula", canonical_name="Luiz Inácio Lula da Silva", entity_type="politician", aliases=("Lula",))
ENTITIES = [LULA]

LEXICON = Lexicon(
    positive_re=re.compile(r"\b(aprovou)\b", re.IGNORECASE),
    negative_re=re.compile(r"\b(rejeitou)\b", re.IGNORECASE),
)

MODEL_NAME = "lexicon-baseline"
MODEL_VERSION = "1"
FUZZY_THRESHOLD = 0.85

# Parágrafo único (sem linha em branco -- fica como um só parágrafo depois
# da segmentação), grande o suficiente para passar do MIN_PARAGRAPH_CHARS,
# mencionando a entidade (via alias "Lula") e um termo positivo do léxico
# ("aprovou"), para gerar exatamente uma linha de classificação.
DEFAULT_FULL_TEXT = (
    "O presidente Lula aprovou nesta semana uma nova medida econômica que promete "
    "impactar diretamente o orçamento federal para o próximo ano."
)


def _build_payload_bytes(**overrides) -> bytes:
    """Monta o JSON do payload do scraper (ver src/payload.py) com valores
    padrão válidos, sobrescrevendo só os campos passados em `overrides` --
    evita repetir o dict inteiro em cada teste."""
    payload = {
        "schema_version": 1,
        "job_id": 42,
        "article_id": 123,
        "pipeline_version": "lexicon-baseline-v1",
        "title": "Governo anuncia nova medida",
        "full_text": DEFAULT_FULL_TEXT,
        "canonical_link": "https://exemplo.com/noticia-123",
        "source_name": "Portal Exemplo",
        "published_at": "2026-08-01T10:00:00-03:00",
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def _handle(conn, raw_body: bytes) -> message_handler.HandleResult:
    return message_handler.handle_message(
        conn,
        raw_body,
        entities=ENTITIES,
        lexicon=LEXICON,
        fuzzy_threshold=FUZZY_THRESHOLD,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
    )


def test_redelivery_of_same_message_is_ignored_by_precheck(db_conn):
    # Cenário obrigatório (context.md, seção 10): reentrega da MESMA
    # mensagem (mesmo article_id + pipeline_version) não pode duplicar nada.
    # Aqui a segunda chamada é pega pela checagem prévia
    # (classification_store.already_processed), antes de rodar o pipeline
    # de novo à toa.
    entities_store.sync_entities(db_conn, ENTITIES)
    db_conn.commit()

    raw_body = _build_payload_bytes(article_id=101, pipeline_version="v1")

    first = _handle(db_conn, raw_body)
    assert first.already_processed is False
    assert first.run_id is not None
    assert _count(db_conn, "classification_runs") == 1
    assert _count(db_conn, "classifications") == 1
    assert _count(db_conn, "analyser_notifications") == 1

    second = _handle(db_conn, raw_body)
    assert second == message_handler.HandleResult(run_id=None, already_processed=True)

    # Nenhuma duplicata em nenhuma das três tabelas envolvidas na transação
    # de persistência.
    assert _count(db_conn, "classification_runs") == 1
    assert _count(db_conn, "classifications") == 1
    assert _count(db_conn, "analyser_notifications") == 1


def test_unique_constraint_race_is_handled_as_idempotent_not_as_error(db_conn, monkeypatch):
    # Cenário de corrida documentado no comentário de
    # classification_store.persist_classification_result: duas entregas
    # concorrentes do mesmo job passam pela checagem prévia (nenhuma via' a
    # outra ainda), e é a constraint UNIQUE (article_id, pipeline_version)
    # em classification_runs quem evita a duplicata de verdade.
    #
    # Para reproduzir isso de forma determinística (sem threads reais):
    # 1. insere manualmente uma linha "concorrente" já commitada para o
    #    mesmo (article_id, pipeline_version) do payload;
    # 2. força handle_message a pular a checagem prévia via monkeypatch,
    #    simulando a janela de corrida em que already_processed() ainda não
    #    tinha visto essa linha quando foi consultado.
    # Isso garante que o INSERT dentro de persist_classification_result vai
    # de fato colidir com a constraint e produzir uma UniqueViolation real.
    entities_store.sync_entities(db_conn, ENTITIES)
    db_conn.commit()

    article_id = 555
    pipeline_version = "v1"

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO classification_runs (
                job_id, article_id, pipeline_version, content_type,
                model_name, model_version, entity_match_threshold
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (999_001, article_id, pipeline_version, "news_article", MODEL_NAME, MODEL_VERSION, FUZZY_THRESHOLD),
        )
    db_conn.commit()

    monkeypatch.setattr(classification_store, "already_processed", lambda *args, **kwargs: False)

    raw_body = _build_payload_bytes(job_id=42, article_id=article_id, pipeline_version=pipeline_version)

    result = _handle(db_conn, raw_body)

    # Não propaga UniqueViolation -- vira sucesso idempotente.
    assert result == message_handler.HandleResult(run_id=None, already_processed=True)

    # Só a linha concorrente pré-existente continua lá; nada foi duplicado
    # nem meio-gravado (a transação de persist_classification_result foi
    # revertida inteira pela exceção).
    assert _count(db_conn, "classification_runs") == 1
    assert _count(db_conn, "classifications") == 0
    assert _count(db_conn, "analyser_notifications") == 0


def test_malformed_json_is_rejected_without_side_effects(db_conn):
    with pytest.raises(message_handler.RejectedMessage):
        _handle(db_conn, b"{isso nao e json valido")

    assert _count(db_conn, "classification_runs") == 0


def test_payload_missing_required_field_is_rejected_without_side_effects(db_conn):
    payload = json.loads(_build_payload_bytes())
    del payload["canonical_link"]
    raw_body = json.dumps(payload).encode("utf-8")

    with pytest.raises(message_handler.RejectedMessage):
        _handle(db_conn, raw_body)

    assert _count(db_conn, "classification_runs") == 0
