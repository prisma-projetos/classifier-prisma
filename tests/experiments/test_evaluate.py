"""Testes do experiments/evaluate.py com dados sintéticos. As métricas e as
predições têm testes próprios; aqui só se verifica a orquestração e a
recusa de sobrescrever a saída."""
import hashlib
import json

import pytest

import evaluate
import metrics

TEXT = "Lula criticou o PT. Ele saiu."
REFERENCE = [{
    "article_id": "1", "paragraph_index": 0, "text": TEXT, "split": None,
    "mentions": [{"start": 0, "end": 4, "text": "Lula", "type": "DIR", "entity_id": "1"},
                 {"start": 20, "end": 23, "text": "Ele", "type": "IND", "entity_id": "1"}],
    "teor": {"1": "negativo"}, "annotator": "referencia",
}]


def test_run_systems_nao_entrega_rotulos_e_usa_d15_e_modos_de_pares(monkeypatch):
    calls = []

    def fake_mentions(records, entities, *, system, include_party_descriptions):
        records = list(records)
        calls.append(("mentions", system, include_party_descriptions, records))
        return [{**r, "mentions": [], "teor": {}, "prediction_config": {"system": system}} for r in records]

    def fake_lexicon(records, *, pairs, lexicon_path):
        calls.append(("lexicon", pairs, records))
        return records

    monkeypatch.setattr(evaluate.mentions, "predict_mentions", fake_mentions)
    monkeypatch.setattr(evaluate.teor, "predict_lexicon", fake_lexicon)

    predictions = evaluate.run_systems(REFERENCE, [], "lexico.yaml")

    mention_calls = [c for c in calls if c[0] == "mentions"]
    assert [(c[1], c[2]) for c in mention_calls] == [("matcher", False), ("recency", False)]
    assert all(set(r) == {"article_id", "paragraph_index", "text"} for c in mention_calls for r in c[3])
    assert [c[1] for c in calls if c[0] == "lexicon"] == ["mentions", "mentions", "gold"]
    assert predictions["lexico_pares_referencia"] is REFERENCE
    assert set(predictions) == set(evaluate.PREDICTION_FILES)


def test_evaluate_chama_metricas_nos_modos_certos(monkeypatch):
    calls = []

    def fake_mention_scores(gold, predicted, *, mode):
        calls.append(("mention_scores", mode, predicted[0]["tag"]))
        return {"overall": {"f1": 0.1}, "by_type": {"IND": {"f1": 0.2}, "DIR": {"f1": 0.3}}}

    def fake_teor_scores(gold, predicted, *, end_to_end):
        calls.append(("teor_scores", end_to_end, predicted[0]["tag"]))
        return {"end_to_end": end_to_end}

    def fake_bootstrap(gold, predicted, score_fn, *, n_resamples, seed):
        calls.append(("bootstrap", n_resamples, seed))
        return {"value": score_fn(gold, predicted)}

    monkeypatch.setattr(metrics, "mention_scores", fake_mention_scores)
    monkeypatch.setattr(metrics, "teor_scores", fake_teor_scores)
    monkeypatch.setattr(metrics, "bootstrap_by_article", fake_bootstrap)
    predictions = {key: [{"tag": key}] for key in evaluate.PREDICTION_FILES}

    result = evaluate.evaluate(REFERENCE, predictions, n_resamples=7, seed=3)

    for system in ("matcher", "recency"):
        for mode in ("exact", "partial"):
            item = result["mentions"][system][mode]
            assert ("mention_scores", mode, f"{system}_lexico") in calls
            assert item["bootstrap"]["overall_f1"] == {"value": 0.1}
            assert item["bootstrap"]["ind_f1"] == {"value": 0.2}
        assert result["fim_a_fim"][system] == {"end_to_end": True}
    assert ("teor_scores", False, "lexico_pares_referencia") in calls
    assert result["teor_isolado"] == {"end_to_end": False}
    assert [c for c in calls if c[0] == "bootstrap"] == [("bootstrap", 7, 3)] * 8


def test_write_evaluation_recusa_sobrescrever_e_nao_grava_nada(tmp_path):
    existing = tmp_path / "report.md"
    existing.write_text("antigo", encoding="utf-8")

    with pytest.raises(ValueError, match="já existe"):
        evaluate.write_evaluation({}, {key: [] for key in evaluate.PREDICTION_FILES}, tmp_path)

    assert existing.read_text(encoding="utf-8") == "antigo"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.md"]


def test_main_recusa_saida_existente_antes_de_calcular(tmp_path, monkeypatch, capsys):
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(evaluate, "build_report", lambda **_: pytest.fail("não deveria calcular"))

    assert evaluate.main(["--saida", str(tmp_path)]) == 2
    assert "já existe" in capsys.readouterr().err


def test_fluxo_sintetico_grava_saidas_com_hashes(tmp_path):
    entities = tmp_path / "entidades.yaml"
    entities.write_text(
        "entities:\n"
        "- {entity_id: '1', entity_type: politico, nome_principal: LULA, aliases: [LULA]}\n"
        "- {entity_id: '13', entity_type: partido, nome_principal: PT, aliases: [PT]}\n",
        encoding="utf-8")
    lexicon = tmp_path / "lexicon.yaml"
    lexicon.write_text("positive:\n  - elogiou\nnegative:\n  - criticou\n", encoding="utf-8")
    reference = tmp_path / "referencia.jsonl"
    reference.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in REFERENCE), encoding="utf-8")

    report, predictions = evaluate.build_report(reference_path=reference, entities_path=entities,
                                                lexicon_path=lexicon, n_resamples=5, seed=42, repeats=1)
    out = tmp_path / "saida"
    evaluate.write_evaluation(report, predictions, out)

    written = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert written["inputs"]["referencia"]["sha256"] == hashlib.sha256(reference.read_bytes()).hexdigest()
    for name, item in written["outputs"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == item["sha256"]
    assert "## 4. Custo" in (out / "report.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        evaluate.write_evaluation(report, predictions, out)
