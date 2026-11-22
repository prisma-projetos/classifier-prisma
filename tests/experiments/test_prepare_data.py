"""Seleção de notícias e atribuição sem acesso a banco ou dados anotados."""
from collections import Counter
import csv
import json
import random

import pytest

from prepare_data import (REVIEWERS, duplicate_groups, make_assignments, prepare,
                          replace_candidates, select_candidates)


def articles(count=80):
    return {str(i): {"id": str(i), "extraction_status": "success",
                     "full_text": f"Texto independente da notícia {i}.",
                     "canonical_link": f"https://news.example/{i}"}
            for i in range(count)}


def test_sixty_articles_six_shared_nine_exclusive_each():
    rows = articles()
    pool, selected, assignments, exclusions = select_candidates(rows, set(rows), {"0"}, {"1"})
    assert len(pool) == 78
    assert len(set(selected)) == 60
    assert not set(selected) & {"0", "1"}
    assert len(assignments) == 90
    shared = Counter(r["article_id"] for r in assignments if r["is_shared"] == "true")
    assert len(shared) == 6
    assert set(shared.values()) == {6}
    exclusive = [r for r in assignments if r["is_shared"] == "false"]
    assert len({r["article_id"] for r in exclusive}) == 54
    assert not shared.keys() & {r["article_id"] for r in exclusive}
    assert Counter(r["reviewer"] for r in exclusive) == {r: 9 for r in REVIEWERS}
    assert Counter(r["reviewer"] for r in assignments) == {r: 15 for r in REVIEWERS}
    assert select_candidates(dict(reversed(list(rows.items()))), set(rows), {"0"}, {"1"}) == (
        pool, selected, assignments, exclusions)


def test_url_text_and_transitive_duplicates_never_leak():
    rows = articles()
    rows["2"]["canonical_link"] = "http://NEWS.example/0/?utm_source=a#anchor"
    rows["3"]["full_text"] = rows["2"]["full_text"].upper() + "\n"
    rows["4"]["html_canonical_link"] = rows["3"]["canonical_link"]
    rows["5"]["full_text"] = rows["1"]["full_text"]
    rows["7"]["full_text"] = rows["6"]["full_text"]
    assert {"0", "2", "3", "4"} in duplicate_groups(rows)
    pool, selected, _, exclusions = select_candidates(rows, set(rows), {"0"}, {"1"})
    assert not set(pool) & {"0", "1", "2", "3", "4", "5", "7"}
    assert "6" in pool
    assert not set(selected) & {"0", "1", "2", "3", "4", "5", "7"}
    assert {r["article_id"] for r in exclusions} == {"0", "1", "2", "3", "4", "5", "7"} - {"0"}


def test_rejects_gaps_failed_extraction_and_small_pool():
    rows = articles()
    with pytest.raises(ValueError, match="IDs ausentes"):
        select_candidates(rows, set(rows), {"missing"}, set())
    rows["1"]["extraction_status"] = "failed"
    with pytest.raises(ValueError, match="extração utilizável"):
        select_candidates(rows, set(rows), {"0"}, set())
    with pytest.raises(ValueError, match="Pool insuficiente"):
        select_candidates(articles(61), set(map(str, range(61))), {"0"}, {"1"})


def test_replacement_preserves_assignment_slots_and_unaffected_articles():
    rows = articles()
    pool, selected, _, _ = select_candidates(rows, set(rows), {"0"}, {"1"})
    removed = {selected[4], selected[6], selected[17]}
    revised, replacements = replace_candidates(pool, selected, removed)
    assert len(revised) == len(set(revised)) == 60
    assert not set(revised) & removed
    assert all(new == old for new, old in zip(revised, selected) if old not in removed)
    assert len(set(revised) - set(selected)) == len(removed)
    assert set(revised) <= set(pool)
    assert [row["position"] for row in replacements] == [4, 6, 17]
    assert replacements[0]["reviewers"] == list(REVIEWERS)
    assert replacements[1]["reviewers"] == [REVIEWERS[0]]
    historical_order = sorted(pool)
    random.Random(42).shuffle(historical_order)
    assert [row["article_id"] for row in replacements] == historical_order[60:63]
    original_slots = [(r["reviewer"], r["is_shared"]) for r in make_assignments(selected)]
    assert [(r["reviewer"], r["is_shared"]) for r in make_assignments(revised)] == original_slots
    assert replace_candidates(list(reversed(pool)), selected, removed) == (revised, replacements)
    with pytest.raises(ValueError, match="lista não vazia"):
        replace_candidates(pool, selected, {"missing"})
    with pytest.raises(ValueError, match="insuficiente"):
        replace_candidates(selected, selected, removed)


def test_preparation_outputs_are_reproducible_without_labels_or_split(tmp_path):
    raw = tmp_path / "raw.csv"
    rows = [{**row, "source_name": "Veículo de teste"} for row in articles().values()]
    with raw.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = tmp_path / "manifest.csv"
    manifest.write_text("article_id\n0\n", encoding="utf-8")
    pilot = tmp_path / "pilot.txt"
    pilot.write_text("1\n", encoding="utf-8")
    eligible = tmp_path / "eligible.txt"
    eligible.write_text("\n".join(articles()) + "\n", encoding="utf-8")
    provenance = tmp_path / "provenance.json"
    provenance.write_text("{}", encoding="utf-8")
    snapshot = tmp_path / "entities.yaml"
    snapshot.write_text("generated_at: '2026-08-29T19:29:23Z'\nentities:\n"
                        "- entity_type: politico\n  aliases: [Nome]\n", encoding="utf-8")
    output = tmp_path / "output"
    args = (raw, manifest, pilot, eligible, provenance, snapshot, output)
    prepare(*args)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    prepare(*args)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    with (output / "candidates.csv").open() as stream:
        candidates = list(csv.DictReader(stream))
    assert len(candidates) == 60
    assert set(r["status"] for r in candidates) == {"PROVISORIA"}
    assert "split" not in candidates[0] and "label" not in candidates[0]
    assert json.loads((output / "metadata.json").read_text())["snapshot"]["definitive"] is False

    selected = (output / "candidate_ids.txt").read_text().splitlines()
    removed = {selected[4], selected[6]}
    replace_ids = tmp_path / "replace.txt"
    replace_ids.write_text("\n".join(sorted(removed)) + "\n", encoding="utf-8")
    revised_output = tmp_path / "revised"
    prepare(raw, manifest, pilot, eligible, provenance, snapshot, revised_output,
            replace_ids=replace_ids, base=output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    revised = (revised_output / "candidate_ids.txt").read_text().splitlines()
    assert len(revised) == len(set(revised)) == 60
    assert not set(revised) & removed
    assert set(selected) - removed <= set(revised)
    assert removed <= read_csv_ids(revised_output / "exclusions.csv")
    report = json.loads((revised_output / "replacement-report.json").read_text())
    assert report["validation"]["removed_ids_absent"] is True
    assert len(report["replacements"]) == 2
    assert report["scope_review"].startswith("PENDENTE")
    with pytest.raises(ValueError, match="saída já existe"):
        prepare(raw, manifest, pilot, eligible, provenance, snapshot, revised_output,
                replace_ids=replace_ids, base=output)
    with pytest.raises(ValueError, match="mesma semente"):
        prepare(raw, manifest, pilot, eligible, provenance, snapshot, tmp_path / "wrong-seed",
                seed=99, replace_ids=replace_ids, base=output)
    second_removed = revised[8]
    replace_ids.write_text(second_removed + "\n", encoding="utf-8")
    second_output = tmp_path / "second-revision"
    prepare(raw, manifest, pilot, eligible, provenance, snapshot, second_output,
            replace_ids=replace_ids, base=revised_output)
    historical_order = (output / "pool_ids.txt").read_text().splitlines()
    random.Random(42).shuffle(historical_order)
    assert json.loads((second_output / "replacement-report.json").read_text())[
        "replacements"][0]["article_id"] == historical_order[62]
    assert not set((second_output / "candidate_ids.txt").read_text().splitlines()) & (
        removed | {second_removed})
    assert removed | {second_removed} <= read_csv_ids(second_output / "exclusions.csv")


def read_csv_ids(path):
    with path.open(encoding="utf-8", newline="") as stream:
        return {row["article_id"] for row in csv.DictReader(stream)}
