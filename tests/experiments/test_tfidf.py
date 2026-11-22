"""Comportamento do TF-IDF experimental sem depender de dados anotados reais."""
from copy import deepcopy

import pytest

pytest.importorskip("sklearn", reason="TF-IDF roda no ambiente isolado de experimentos")

import teor


CLASSES = ("positivo", "negativo", "neutro")
PILOT_IDS = ("145", "159", "146", "160", "175")


def _mention(start, end, entity_id="alvo"):
    return {"start": start, "end": end, "entity_id": entity_id, "type": "DIR"}


def _record(article_id, index, text, label, *, split="train", mention=True, entity="alvo"):
    start = text.index("Candidato") if mention else None
    mentions = [_mention(start, start + len("Candidato"), entity)] if mention else []
    return {
        "article_id": str(article_id),
        "paragraph_index": index,
        "text": text,
        "mentions": mentions,
        "teor": {entity: label},
        "split": split,
    }


def _training_records():
    records = []
    for label, cue in zip(CLASSES, ("apoiou", "rejeitou", "debateu")):
        for index in range(2):
            records.append(_record(
                f"treino-{label}-{index}", 0,
                f"Candidato {cue} a proposta única {label} exemplo{index}.", label,
            ))
    return records


def test_mark_target_substitui_todas_as_mencoes_do_alvo_e_preserva_outros_alvos():
    text = "Candidato encontrou Candidato e Partido."
    first = text.index("Candidato")
    second = text.index("Candidato", first + 1)
    party = text.index("Partido")
    mentions = [
        _mention(first, first + len("Candidato"), 1),
        _mention(second, second + len("Candidato"), "1"),
        _mention(party, party + len("Partido"), "partido"),
    ]

    marked = teor.mark_target(text, mentions, 1)

    assert marked == "[ALVO] encontrou [ALVO] e Partido."
    assert marked.count("[ALVO]") == 2


def test_mark_target_usa_offsets_unicode_originais_e_mescla_sobreposicao_do_alvo():
    text = "A ação de Candidato foi pública."
    start = text.index("Candidato")
    mentions = [
        _mention(start, start + len("Candidato")),
        _mention(start + 4, start + len("Candidato")),
    ]

    assert teor.mark_target(text, mentions, "alvo") == "A ação de [ALVO] foi pública."


@pytest.mark.parametrize("start,end", [(-1, 3), (2, 99), (4, 3)])
def test_mark_target_rejeita_offsets_invalidos(start, end):
    with pytest.raises(ValueError):
        teor.mark_target("texto", [_mention(start, end)], "alvo")


def test_mark_target_sem_span_preserva_texto_para_par_de_sujeito_oculto():
    text = "O presidente afirmou que a proposta é boa."

    assert teor.mark_target(text, [], "alvo") == text


def test_fit_treina_so_no_split_train_e_nao_aprende_vocabulario_da_validacao():
    records = _training_records()
    before = deepcopy(records)

    model = teor.fit_tfidf(records)
    vocabulary = model.named_steps["tfidf"].vocabulary_

    assert records == before
    assert set(model.classes_) == set(CLASSES)
    # Tokens presentes apenas em holdout não podem entrar no ajuste do vetor.
    assert "tokenexclusivovalidacao_xyz" not in vocabulary
    assert "tokenexclusivoteste_xyz" not in vocabulary
    metadata = model.training_metadata_
    assert metadata["label_counts"] == {label: 2 for label in CLASSES}


def test_fit_exclui_indeterminado_mas_preserva_par_sem_mencao_como_nao_marcado():
    records = _training_records()
    records.append(_record(
        "treino-oculto", 0, "O presidente afirmou que a proposta é boa.",
        "positivo", mention=False,
    ))
    records.append(_record(
        "treino-indeterminado", 0, "Candidato gerou dúvida na equipe.",
        "indeterminado",
    ))

    model = teor.fit_tfidf(records)

    assert model.training_metadata_["excluded_indeterminado"] == 1
    assert model.training_metadata_["unmarked_pairs"] == 1
    assert model.training_metadata_["label_counts"] == {
        "positivo": 3, "negativo": 2, "neutro": 2,
    }
    assert model.training_metadata_["pairs"] == 7


@pytest.mark.parametrize("split", ["validation", "test"])
def test_fit_rejeita_entrada_de_validacao_ou_teste(split):
    records = _training_records() + [
        _record(f"vazamento-{split}", 0, "Candidato apoiou proposta.", "positivo", split=split)
    ]

    with pytest.raises(ValueError, match="train|treino|split"):
        teor.fit_tfidf(records)


def test_fit_exige_duas_classes_e_recusa_rótulo_nao_relacionado():
    one_class = [
        _record(f"um-{index}", 0, f"Candidato apoiou proposta {index}.", "positivo")
        for index in range(2)
    ]
    with pytest.raises(ValueError, match="classe|class"):
        teor.fit_tfidf(one_class)

    two_classes = [record for record in _training_records() if record["teor"]["alvo"] != "neutro"]
    two_class_model = teor.fit_tfidf(two_classes)
    assert set(two_class_model.classes_) == {"positivo", "negativo"}

    with pytest.raises(ValueError, match="rótulo|label|teor"):
        teor.fit_tfidf(_training_records() + [
            _record("nao-relacionado", 0, "Candidato fora do escopo.", "nao_relacionado")
        ])


@pytest.mark.parametrize("article_id", PILOT_IDS)
def test_fit_recusa_noticias_do_piloto(article_id):
    records = _training_records() + [
        _record(article_id, 0, "Candidato apoiou a proposta.", "positivo")
    ]

    with pytest.raises(ValueError, match="piloto|pilot|145|159|146|160|175"):
        teor.fit_tfidf(records)


def test_predict_gold_inclui_par_oculto_ignora_valores_do_gabarito_e_nao_muta_entrada():
    model = teor.fit_tfidf(_training_records())
    record = {
        "article_id": "novo-id",
        "paragraph_index": 0,
        "text": "O presidente afirmou que a proposta é boa.",
        "mentions": [],
        "teor": {"alvo": "positivo", "alvo-oculto": "indeterminado"},
        "split": "test",
    }
    before = deepcopy(record)

    altered = deepcopy(record)
    altered["teor"] = {"alvo": "negativo", "alvo-oculto": "neutro"}
    first = teor.predict_tfidf(model, [record], pairs="gold")[0]
    second = teor.predict_tfidf(model, [altered], pairs="gold")[0]

    assert record == before
    assert first["teor"] == second["teor"]
    assert set(first["teor"]) == {"alvo", "alvo-oculto"}
    assert first["teor"] != before["teor"]
    assert first["prediction_config"]["pairs"] == "gold"


def test_predict_mentions_usa_pares_previstos_e_nao_chaves_gold():
    model = teor.fit_tfidf(_training_records())
    text = "Candidato apoiou a proposta."
    start = text.index("Candidato")
    record = {
        "article_id": "id-novo",
        "paragraph_index": 3,
        "text": text,
        "mentions": [_mention(start, start + len("Candidato"), "previsto")],
        "teor": {"gold-oculto": "positivo"},
        "split": "test",
    }

    prediction = teor.predict_tfidf(model, [record], pairs="mentions")[0]

    assert set(prediction["teor"]) == {"previsto"}
    assert prediction["prediction_config"]["pairs"] == "mentions"


def test_predict_rejeita_noticia_ja_usada_no_treino_com_split_de_holdout():
    train = _training_records()
    model = teor.fit_tfidf(train)
    record = _record(
        train[0]["article_id"], 1, "Candidato apoiou outra proposta.", "positivo",
        split="test",
    )

    with pytest.raises(ValueError, match="treino|validação/teste"):
        teor.predict_tfidf(model, [record], pairs="gold")


def test_fit_e_predict_sinteticos_sao_reprodutiveis_e_emitem_somente_classes_suportadas():
    train = _training_records()
    record = _record(
        "id-fresco", 0, "Candidato debateu uma proposta nova.", "indeterminado",
        split="test",
    )

    first_model = teor.fit_tfidf(train)
    second_model = teor.fit_tfidf(train)
    first = teor.predict_tfidf(first_model, [record], pairs="gold")
    second = teor.predict_tfidf(second_model, [record], pairs="gold")

    assert first == second
    assert first[0]["teor"]["alvo"] in CLASSES

