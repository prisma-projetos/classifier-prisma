"""Snapshot DEV e revalidação isolados; nenhuma conexão real nos testes."""
import csv
import json
from unittest.mock import Mock

import pytest
import yaml

import export_snapshot as snapshot
from entities_store import Entity


def document():
    return {"generated_at": "2026-11-10T00:00:00Z", "source_database": "prisma-dev",
            "scope_cargos": ["PRESIDENTE"], "entities": [
                {"entity_id": "100", "entity_type": "politico", "cargo": "PRESIDENTE",
                 "nome_principal": "Pessoa Teste", "aliases": ["Pessoa Teste"]},
                {"entity_id": "13", "entity_type": "partido", "cargo": None,
                 "nome_principal": "Partido Teste", "aliases": ["PT"]}]}


def test_connection_overrides_db_without_changing_environment(monkeypatch):
    values = {"POSTGRES_HOST": "example", "POSTGRES_PORT": "5432",
              "POSTGRES_DB": "outro", "POSTGRES_USER": "usuario", "POSTGRES_PASSWORD": "segredo"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    connect = Mock()
    monkeypatch.setattr(snapshot.psycopg, "connect", connect)
    snapshot.connect_readonly()
    assert connect.call_args.kwargs == {
        "host": "example", "port": "5432", "dbname": "prisma-dev", "user": "usuario",
        "password": "segredo", "connect_timeout": 5, "options": "-c default_transaction_read_only=on"}
    assert snapshot.os.environ["POSTGRES_DB"] == "outro"


@pytest.mark.parametrize("database", ["prisma-dev", "outro"])
def test_target_checked_before_queries_and_always_rolls_back(monkeypatch, database):
    conn, cursor = Mock(), Mock()
    conn.cursor.return_value.__enter__ = Mock(return_value=cursor)
    conn.cursor.return_value.__exit__ = Mock(return_value=False)
    cursor.fetchone.return_value = (database,)
    monkeypatch.setattr(snapshot, "connect_readonly", lambda: conn)
    monkeypatch.setattr(snapshot, "load_cargos_from_settings", lambda: ["PRESIDENTE"])

    def load_entities(connection, cargos):
        assert connection.read_only is True
        assert connection.isolation_level == snapshot.psycopg.IsolationLevel.REPEATABLE_READ
        return [Entity("100", "Pessoa Teste", "politico", ("Pessoa Teste",))]

    loader = Mock(side_effect=load_entities)
    monkeypatch.setattr(snapshot, "load_active_entities_from_db", loader)
    monkeypatch.setattr(snapshot, "load_cargo_by_titulo", lambda *_: {"100": "PRESIDENTE"})
    if database == "prisma-dev":
        assert snapshot.collect_snapshot()["source_database"] == "prisma-dev"
        loader.assert_called_once()
    else:
        with pytest.raises(ValueError, match="fora do alvo"):
            snapshot.collect_snapshot()
        loader.assert_not_called()
    cursor.execute.assert_called_once_with("SELECT current_database()")
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()
    conn.commit.assert_not_called()


def test_integrity_rejects_duplicate_id_and_invalid_cargo():
    doc = document()
    snapshot.validate_document(doc)
    doc["entities"].append(doc["entities"][0])
    with pytest.raises(ValueError, match="duplicados"):
        snapshot.validate_document(doc)
    doc = document()
    doc["entities"][0]["cargo"] = "DEPUTADO"
    with pytest.raises(ValueError, match="Cargo"):
        snapshot.validate_document(doc)


def test_export_and_revalidation_keep_previous_snapshot_and_selection(tmp_path, monkeypatch):
    new, audit, result = (tmp_path / name for name in ("new.yaml", "audit.json", "result.json"))
    previous, ids, raw = (tmp_path / name for name in ("old.yaml", "ids.txt", "raw.csv"))
    old_doc = document()
    old_doc["entities"] = old_doc["entities"][1:]
    previous.write_text(yaml.safe_dump(old_doc), encoding="utf-8")
    ids.write_text("1\n2\n", encoding="utf-8")
    with raw.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "extraction_status", "full_text"])
        writer.writeheader()
        writer.writerows([
            {"id": "1", "extraction_status": "success", "full_text": "Pessoa Teste anunciou seu novo programa."},
            {"id": "2", "extraction_status": "success", "full_text": "Choveu muito nesta cidade durante a madrugada."}])
    original = {p: p.read_bytes() for p in (previous, ids, raw)}
    monkeypatch.setattr(snapshot, "collect_snapshot", document)
    exported = snapshot.export_snapshot(new, audit)
    assert exported["snapshot_sha256"] == snapshot.sha256(new)
    assert exported["entities"] == 2
    validated = snapshot.revalidate_candidates(new, raw, ids, previous, result)
    assert validated["summary"] == {"articles": 2, "with_direct_match": 1,
                                     "without_direct_match": ["2"], "entities_covered": 1}
    assert validated["new_entity_ids"] == ["100"]
    assert validated["articles"][1]["requires_review"] is True
    assert validated["selection_modified"] is False
    assert json.loads(result.read_text()) == validated
    assert all(p.read_bytes() == content for p, content in original.items())


def test_driver_errors_do_not_print_connection_details(monkeypatch, capsys):
    monkeypatch.setattr(snapshot, "load_dotenv", Mock())
    monkeypatch.setattr(snapshot, "export_snapshot", Mock(side_effect=snapshot.psycopg.OperationalError(
        "password=private-secret host=private-host")))
    assert snapshot.main() == 1
    output = capsys.readouterr().err
    assert "private" not in output
    assert "prisma-dev" in output
