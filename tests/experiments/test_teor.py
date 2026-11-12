"""Regressões do adaptador do léxico de produção para pares experimentais."""

import copy

import teor


def _lexicon(tmp_path):
    path = tmp_path / "lexicon.yaml"
    path.write_text("positive:\n  - elogiou\nnegative:\n  - criticou\n", encoding="utf-8")
    return path


def test_modo_gold_usa_chaves_sem_copiar_rotulos_e_funciona_sem_mencoes(tmp_path):
    record = {
        "article_id": "a", "paragraph_index": 0, "text": "Lula criticou o plano.",
        "mentions": [], "teor": {"1": "positivo", "2": "indeterminado"},
    }

    prediction = teor.predict_lexicon([record], pairs="gold", lexicon_path=_lexicon(tmp_path))[0]

    assert prediction["teor"] == {"1": "negativo", "2": "negativo"}
    assert prediction["teor"] != record["teor"]
    assert prediction["mentions"] == []
    assert prediction["prediction_config"]["pairs"] == "gold"


def test_modo_mentions_deduplica_entidade_e_aplica_classe_a_cada_alvo(tmp_path):
    record = {
        "article_id": "a", "paragraph_index": 0, "text": "Lula elogiou a proposta.",
        "mentions": [
            {"start": 0, "end": 4, "entity_id": 1, "type": "DIR"},
            {"start": 0, "end": 4, "entity_id": "1", "type": "DIR"},
            {"start": 0, "end": 9, "entity_id": "2", "type": "DIR"},
        ],
        "teor": {"1": "negativo"},
    }

    prediction = teor.predict_lexicon([record], lexicon_path=_lexicon(tmp_path))[0]

    assert prediction["teor"] == {"1": "positivo", "2": "positivo"}
    assert prediction["prediction_config"]["pairs"] == "mentions"


def test_paragrafo_sem_alvos_nao_cria_pares_de_teor(tmp_path):
    records = [
        {"article_id": "a", "paragraph_index": 0, "text": "criticou", "mentions": []},
        {"article_id": "a", "paragraph_index": 1, "text": "elogiou", "mentions": [], "teor": {}},
    ]

    predictions = teor.predict_lexicon(records, lexicon_path=_lexicon(tmp_path))

    assert [prediction["teor"] for prediction in predictions] == [{}, {}]


def test_predicao_preserva_entrada_e_nao_altera_mencoes_aninhadas(tmp_path):
    record = {
        "article_id": "a", "paragraph_index": 0, "text": "Lula criticou a proposta.",
        "mentions": [{"start": 0, "end": 4, "entity_id": "1", "metadata": {"source": "gold"}}],
        "teor": {"1": "positivo"}, "split": "test",
        "prediction_config": {"system": "fixture"},
    }
    before = copy.deepcopy(record)

    prediction = teor.predict_lexicon([record], lexicon_path=_lexicon(tmp_path))[0]

    assert record == before
    assert prediction["mentions"] == record["mentions"]
    assert prediction["mentions"] is not record["mentions"]
    assert prediction["mentions"][0]["metadata"] is not record["mentions"][0]["metadata"]
    assert prediction["teor"] == {"1": "negativo"}
    assert prediction["split"] == "test"
