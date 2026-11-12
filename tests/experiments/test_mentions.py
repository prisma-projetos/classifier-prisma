"""Regressões D10–D12 para spans e ligação de menções experimentais."""

import pytest

from entities_store import Entity
import mentions


LULA = Entity("lula", "Luiz Inácio Lula da Silva", "politician", ("Lula",))
FLAVIO = Entity("flavio", "Flávio Bolsonaro", "politician", ("Flávio",))
PT = Entity("pt", "Partido dos Trabalhadores", "party", ("PT",))
AVANTE = Entity("avante", "Avante", "party", ())


def _record(article, index, text):
    return {"article_id": article, "paragraph_index": index, "text": text}


def _predict(records, entities, system="recency", *, include_party_descriptions=False):
    return mentions.predict_mentions(
        records, entities, system=system,
        include_party_descriptions=include_party_descriptions,
    )


def test_normalizacao_decomposta_pontuacao_e_offsets_no_original():
    text = "Jose\u0301—Flávio!"
    normalized, positions = mentions.normalize_with_map(text)
    start, end = normalized.index("flavio"), normalized.index("flavio") + len("flavio")
    original_start, original_end = positions[start][0], positions[end - 1][1]

    assert normalized == "jose flavio"
    assert text[original_start:original_end] == "Flávio"
    result = _predict([_record("a", 0, text)], [FLAVIO])[0]
    direct = result["mentions"][0]
    assert (direct["start"], direct["end"], direct["text"]) == (original_start, original_end, "Flávio")


def test_sigla_de_partido_exige_artigo_e_ignora_parenteses():
    result = _predict([_record("a", 0, "O PT venceu; Lula (PT) comentou.")], [PT])[0]

    assert [m["text"] for m in result["mentions"]] == ["PT"]
    assert result["mentions"][0]["start"] == 2


def test_sigla_dentro_de_parenteses_aninhados_e_ignorada():
    result = _predict([_record("a", 0, "(contexto (extra) o PT) foi citado.")], [PT])[0]

    assert result["mentions"] == []


def test_contracao_antes_de_sigla_e_aceita():
    result = _predict([_record("a", 0, "A decisão do PT foi anunciada.")], [PT])[0]

    assert [m["text"] for m in result["mentions"]] == ["PT"]


def test_nome_comum_de_partido_exige_a_palavra_partido():
    records = [
        _record("a", 0, "Avante, disse o candidato."),
        _record("a", 1, "O partido Avante anunciou a decisão."),
    ]

    result = _predict(records, [AVANTE])

    assert result[0]["mentions"] == []
    assert [m["text"] for m in result[1]["mentions"]] == ["Avante"]


def test_matcher_de_producao_localiza_todas_as_ocorrencias_do_alias_aprovado():
    result = _predict(
        [_record("a", 0, "Lula chegou. Lula discursou.")], [LULA], system="matcher",
    )[0]

    assert [m["text"] for m in result["mentions"]] == ["Lula", "Lula"]
    assert [m["start"] for m in result["mentions"]] == [0, 13]


def test_matcher_de_producao_nao_aplica_filtro_experimental_de_partido():
    result = _predict(
        [_record("a", 0, "(PT) venceu e PT comemorou.")], [PT], system="matcher",
    )[0]

    assert [m["text"] for m in result["mentions"]] == ["PT", "PT"]


def test_correspondencia_fuzzy_aprovada_mantem_span_original():
    entity = Entity("bolsonaro", "Bolsonaro", "politician", ())
    text = "Bolsonnaro!"

    result = _predict([_record("a", 0, text)], [entity])[0]

    mention = result["mentions"][0]
    assert (mention["start"], mention["end"], mention["text"]) == (0, 10, "Bolsonnaro")
    assert mention["entity_id"] == "bolsonaro"
    assert mention["match_type"] == "fuzzy"
    assert mention["score"] >= 0.85


def test_descricao_de_partido_depende_da_opcao_explicitada():
    records = [
        _record("a", 0, "O PT anunciou a medida."),
        _record("a", 1, "A legenda afirmou que haverá mudanças."),
    ]

    excluded = _predict(records, [PT])
    included = _predict(records, [PT], include_party_descriptions=True)

    assert excluded[1]["mentions"] == []
    assert [m["text"] for m in included[1]["mentions"]] == ["A legenda"]
    assert included[1]["mentions"][0]["entity_id"] == "pt"
    assert included[1]["mentions"][0]["type"] == "IND"
    assert included[1]["prediction_config"]["include_party_descriptions"] is True
    assert excluded[1]["prediction_config"]["include_party_descriptions"] is False


def test_pronome_usa_entidade_anterior_mais_proxima_incluindo_partido():
    records = [
        _record("a", 0, "Lula conversou com o PT."),
        _record("a", 1, "Depois, ele respondeu.") ,
    ]

    result = _predict(records, [LULA, PT])

    pronoun = next(m for m in result[1]["mentions"] if m["text"] == "ele")
    assert pronoun["entity_id"] == "pt"
    assert pronoun["type"] == "IND"


def test_145_6_real_tem_previsao_lula_sem_alterar_gold_humano_flavio():
    records = [
        _record("145", 6, "Ao tratar do tema da segurança pública, avaliou que o País perdeu a ‘soberania’ e fez críticas ao governo Lula (PT) que, segundo ele, briga com todo mundo, mas não é capaz de enfrentar o crime organizado."),
    ]

    result = _predict(records, [LULA, FLAVIO, PT])
    pronoun = next(m for m in result[0]["mentions"] if m["text"] == "ele")

    # A heurística liga ao nome mais recente no texto (Lula). O gold humano
    # mantém Flávio nesse caso; não deve ser importado nem corrigido no teste.
    assert pronoun["entity_id"] == "lula"


def test_pronome_segue_entidade_mais_recente_sem_copiar_gold():
    text = "Flávio criticou Lula. Segundo ele, haverá mudanças."
    pronoun_start = text.index("ele")
    record = _record("a", 0, text) | {
        "mentions": [{"start": pronoun_start, "end": pronoun_start + 3, "text": "ele", "type": "IND", "entity_id": "flavio"}],
        "teor": {"flavio": "positivo"},
    }

    prediction = _predict([record], [LULA, FLAVIO])[0]
    pronoun = next(m for m in prediction["mentions"] if m["text"] == "ele")

    assert pronoun["entity_id"] == "lula"
    assert prediction["teor"] == {}
    assert prediction["mentions"] != record["mentions"]


def test_predicao_nao_copia_mencoes_ou_teor_do_gold():
    record = _record("a", 0, "Lula falou.") | {
        "mentions": [{"start": 0, "end": 4, "entity_id": "fake", "type": "IND"}],
        "teor": {"fake": "positivo"},
    }

    prediction = _predict([record], [LULA])[0]

    assert prediction["teor"] == {}
    assert [mention["entity_id"] for mention in prediction["mentions"]] == ["lula"]


def test_recency_nao_usa_entidade_de_paragrafo_futuro_nem_de_outra_noticia():
    # A entrada está fora de ordem para confirmar que os índices controlam a
    # cronologia; o segundo artigo não herda o antecedente do primeiro.
    records = [
        _record("a", 1, "Lula foi citado."),
        _record("a", 0, "Ele não tinha sido citado antes."),
        _record("b", 0, "Ele voltou a falar."),
    ]

    result = _predict(records, [LULA])

    assert result[1]["mentions"] == []
    assert result[2]["mentions"] == []
    assert [m["text"] for m in result[0]["mentions"]] == ["Lula"]


@pytest.mark.parametrize("records", [
    [_record("a", 0, "texto"), _record("a", 0, "duplicado")],
])
def test_paragrafo_duplicado_e_rejeitado(records):
    with pytest.raises(ValueError, match="duplicado"):
        _predict(records, [LULA])
