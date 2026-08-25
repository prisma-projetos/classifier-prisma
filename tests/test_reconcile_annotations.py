from __future__ import annotations

import csv
import sys
from collections import Counter
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import reconcile_annotations as ra  # noqa: E402

# Nomes de revisor usados só pra montar cenários de teste -- não é mais um
# REVIEWERS hardcoded no módulo (ele descobre os arquivos dinamicamente via
# glob), mas os testes de resolve_final_label/fleiss_kappa não dependem de
# arquivo em disco, só de uma lista consistente de identificadores.
REVIEWER_NAMES = [f"revisor_{i}" for i in range(1, 7)]


def test_normalize_label_accepts_canonical_values():
    assert ra.normalize_label("negativo") == "negativo"
    assert ra.normalize_label("  positivo  ") == "positivo"


def test_normalize_label_tolerates_human_variation():
    assert ra.normalize_label("Não Relacionado") == "nao_relacionado"
    assert ra.normalize_label("NEUTRO") == "neutro"


def test_normalize_label_flags_unknown_value_instead_of_guessing():
    result = ra.normalize_label("mais ou menos")
    assert result is not None and result.startswith("INVALIDO:")


def test_resolve_final_label_without_responses_is_pending():
    unit = ra.Unit("u1", "1", "1-0", "lula", "Lula", "train", False, "texto", ["revisor_1"])
    label, needs_review = ra.resolve_final_label(unit)
    assert label == "PENDENTE_RESPOSTA"
    assert needs_review is True


def test_resolve_final_label_unanimous_is_resolved_automatically():
    unit = ra.Unit("u1", "1", "1-0", "lula", "Lula", "train", True, "texto", REVIEWER_NAMES)
    unit.responses = [ra.Response(r, "positivo", 4, "") for r in REVIEWER_NAMES]
    label, needs_review = ra.resolve_final_label(unit)
    assert label == "positivo"
    assert needs_review is False


def test_resolve_final_label_disagreement_is_never_decided_by_majority():
    unit = ra.Unit("u1", "1", "1-0", "lula", "Lula", "train", True, "texto", REVIEWER_NAMES)
    unit.responses = [
        ra.Response("revisor_1", "positivo", None, ""),
        ra.Response("revisor_2", "positivo", None, ""),
        ra.Response("revisor_3", "positivo", None, ""),
        ra.Response("revisor_4", "positivo", None, ""),
        ra.Response("revisor_5", "positivo", None, ""),
        ra.Response("revisor_6", "negativo", None, ""),  # 5 a 1, mas não é maioria automática
    ]
    label, needs_review = ra.resolve_final_label(unit)
    assert label == "PENDENTE_REVISAO"
    assert needs_review is True


def test_fleiss_kappa_is_one_for_perfect_agreement():
    rows = [Counter({"positivo": 6}), Counter({"negativo": 6}), Counter({"neutro": 6})]
    assert ra.fleiss_kappa(rows) == pytest.approx(1.0)


def test_fleiss_kappa_is_low_for_near_random_agreement():
    # 3 categorias distribuídas quase igualmente entre os 6 votos, em várias
    # unidades -- concordância deve ficar bem abaixo da unanimidade.
    rows = [
        Counter({"positivo": 2, "negativo": 2, "neutro": 2}),
        Counter({"positivo": 2, "negativo": 2, "neutro": 2}),
        Counter({"positivo": 2, "negativo": 2, "neutro": 2}),
    ]
    kappa = ra.fleiss_kappa(rows)
    assert kappa is not None
    assert kappa < 0.2


def test_fleiss_kappa_ignores_units_with_a_single_response():
    rows = [Counter({"positivo": 1})]
    assert ra.fleiss_kappa(rows) is None


def test_discover_reviewer_files_uses_real_names_and_ignores_original_divisions(tmp_path, monkeypatch):
    monkeypatch.setattr(ra, "DATA_DIR", tmp_path)

    (tmp_path / "revisor_bea.csv").write_text("paragraph_id,entity_name,label\n", encoding="utf-8")
    (tmp_path / "revisor_anderson.csv").write_text("paragraph_id,entity_name,label\n", encoding="utf-8")
    (tmp_path / "manifest.csv").write_text("unit_id\n", encoding="utf-8")  # não deve ser confundido com revisor

    original_divisions = tmp_path / "original_divisions"
    original_divisions.mkdir()
    (original_divisions / "revisor_1.csv").write_text("paragraph_id,entity_name,label\n", encoding="utf-8")

    files = ra.discover_reviewer_files()
    names = sorted(ra.reviewer_name_from_path(p) for p in files)

    assert names == ["revisor_anderson", "revisor_bea"]


def test_end_to_end_reconciliation_with_tmp_files(tmp_path, monkeypatch):
    data_dir = tmp_path
    monkeypatch.setattr(ra, "DATA_DIR", data_dir)
    monkeypatch.setattr(ra, "MANIFEST_PATH", data_dir / "manifest.csv")

    manifest_rows = [
        {
            "unit_id": "1-0-lula", "article_id": "1", "paragraph_id": "1-0", "entity_id": "lula",
            "entity_name": "Lula", "split": "train", "is_overlap": "True",
            "reviewers": ";".join(REVIEWER_NAMES), "source_name": "", "canonical_link": "",
            "paragraph_text": "Texto de sobreposição, todo mundo anota.",
        },
        {
            "unit_id": "2-0-lula", "article_id": "2", "paragraph_id": "2-0", "entity_id": "lula",
            "entity_name": "Lula", "split": "train", "is_overlap": "False",
            "reviewers": "revisor_1", "source_name": "", "canonical_link": "",
            "paragraph_text": "Texto exclusivo do revisor_1.",
        },
    ]
    with open(data_dir / "manifest.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(manifest_rows[0].keys()))
        writer.writeheader()
        writer.writerows(manifest_rows)

    for i, reviewer in enumerate(REVIEWER_NAMES, start=1):
        rows = [{"paragraph_id": "1-0", "entity_name": "Lula", "paragraph_text": "...", "label": "positivo", "intensity": "4", "notes": ""}]
        if reviewer == "revisor_1":
            rows.append({"paragraph_id": "2-0", "entity_name": "Lula", "paragraph_text": "...", "label": "negativo", "intensity": "", "notes": ""})
        with open(data_dir / f"{reviewer}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["paragraph_id", "entity_name", "paragraph_text", "label", "intensity", "notes"])
            writer.writeheader()
            writer.writerows(rows)

    ra.main()

    with open(data_dir / "consolidated.csv", newline="", encoding="utf-8") as f:
        consolidated = {row["unit_id"]: row for row in csv.DictReader(f)}

    assert consolidated["1-0-lula"]["final_label"] == "positivo"
    assert consolidated["1-0-lula"]["needs_review"] == "False"
    assert consolidated["2-0-lula"]["final_label"] == "negativo"

    report = (data_dir / "agreement_report.md").read_text(encoding="utf-8")
    assert "Fleiss' kappa" in report
