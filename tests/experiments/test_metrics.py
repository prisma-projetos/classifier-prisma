"""Casos calculados à mão para as métricas usadas na avaliação."""

import pytest

import metrics


def _mention(start, end, entity="A", kind="DIR"):
    return {"start": start, "end": end, "text": "x", "type": kind, "entity_id": entity}


def _record(article, index, mentions=None, teor=None):
    return {
        "article_id": article,
        "paragraph_index": index,
        "mentions": mentions or [],
        "teor": teor or {},
    }


def test_match_maximiza_casamentos_um_para_um_em_vez_de_guloso():
    # O primeiro gold casa com os dois previstos; o segundo, apenas com o
    # primeiro. Se um guloso pegar primeiro (0,3) para o primeiro gold, perde
    # o segundo acerto, então o matcher precisa remanejar a associação.
    gold = [_mention(0, 10), _mention(2, 3)]
    predicted = [_mention(0, 3), _mention(3, 10)]

    assert len(metrics.match_mentions(gold, predicted, mode="partial")) == 2


def test_tipo_errado_conta_fp_e_fn_na_metrica_por_tipo():
    result = metrics.mention_scores(
        [_record("1", 0, [_mention(0, 4, kind="IND")])],
        [_record("1", 0, [_mention(0, 4, kind="DIR")])],
    )

    assert result["overall"]["tp"] == 1
    assert result["by_type"]["DIR"]["fp"] == 1
    assert result["by_type"]["IND"]["fn"] == 1


def test_entity_joint_exige_entidade_correta_sem_duplicar_tp():
    gold = [_record("1", 0, [_mention(0, 4, "A")])]
    predicted = [_record("1", 0, [_mention(0, 4, "B")])]

    result = metrics.mention_scores(gold, predicted)

    assert result["overall"]["tp"] == 1
    assert result["overall"]["fp"] == 0
    assert result["linking"] == {"matched": 1, "correct": 0, "accuracy": 0.0}
    assert result["joint"]["tp"] == 0
    assert result["joint"]["fp"] == 1
    assert result["joint"]["fn"] == 1


def test_duplicata_de_predicao_nao_multiplica_true_positive():
    gold = [_record("1", 0, [_mention(0, 4)])]
    predicted = [_record("1", 0, [_mention(0, 4), _mention(0, 4)])]

    result = metrics.mention_scores(gold, predicted)

    assert result["overall"]["tp"] == 1
    assert result["overall"]["fp"] == 1


def test_teor_indeterminado_e_ausencia_sao_excluidos_sem_virar_neutro():
    gold = [_record("1", 0, teor={"A": "indeterminado", "B": "positivo"})]
    predicted = [_record("1", 0, teor={"A": "neutro", "C": "negativo"})]

    isolated = metrics.teor_scores(gold, predicted)
    end_to_end = metrics.teor_scores(gold, predicted, end_to_end=True)

    assert isolated["coverage"]["gold_indeterminado_excluded"] == 1
    # A métrica isolada não pontua o par C, mas informa que ele ficou fora do gold.
    assert isolated["coverage"]["outside_gold_predictions"] == 1
    assert isolated["missing_predictions"]["positivo"] == 1
    assert isolated["confusion_matrix"] == [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
    assert end_to_end["coverage"]["outside_gold_predictions"] == 1
    assert end_to_end["per_class"]["negativo"]["fp"] == 1


def test_fleiss_kappa_confere_exemplo_do_teste_oficial_statsmodels():
    # Dados e kappa=0,210 publicados no teste oficial do statsmodels:
    # https://raw.githubusercontent.com/statsmodels/statsmodels/main/statsmodels/stats/tests/test_inter_rater.py
    counts = [
        [0, 0, 0, 0, 14],
        [0, 2, 6, 4, 2],
        [0, 0, 3, 5, 6],
        [0, 3, 9, 2, 0],
        [2, 2, 8, 1, 1],
        [7, 7, 0, 0, 0],
        [3, 2, 6, 3, 0],
        [2, 5, 3, 2, 2],
        [6, 5, 2, 1, 0],
        [0, 2, 2, 3, 7],
    ]

    assert metrics.fleiss_kappa(counts) == pytest.approx(0.210, abs=0.0005)


def test_fleiss_kappa_com_duas_pessoas_confere_calculo_manual():
    # Concordâncias por unidade: 1, 0 e 1; P_bar=2/3, P_e=1/2.
    assert metrics.fleiss_kappa([[2, 0], [1, 1], [0, 2]]) == pytest.approx(1 / 3)


def test_fleiss_rejeita_numero_variavel_de_avaliadores():
    with pytest.raises(ValueError, match="constante"):
        metrics.fleiss_kappa([[2, 1], [1, 1]])


def test_bootstrap_reamostra_noticia_e_conta_sorteios_repetidos():
    gold = [{**_record("a", 0), "text": "artigo a"}, {**_record("b", 0), "text": "artigo b"}]
    predicted = []
    observed = []

    def score_fn(sample_gold, sample_predicted):
        assert sample_predicted == []
        observed.append([record["text"] for record in sample_gold])
        return len(sample_gold)

    result = metrics.bootstrap_by_article(
        gold, predicted, score_fn, n_resamples=20, seed=17,
    )

    assert result["estimate"] == 2.0
    assert result["n_articles"] == 2
    assert result["seed"] == 17
    assert all(len(sample) == 2 for sample in observed)
    assert any(sample[0] == sample[1] for sample in observed)
    assert metrics.bootstrap_by_article(gold, predicted, lambda a, b: len(a), n_resamples=20, seed=17) == result


@pytest.mark.parametrize("kwargs", [{"n_resamples": 0}, {"confidence": 1.0}])
def test_bootstrap_rejeita_parametros_invalidos(kwargs):
    with pytest.raises(ValueError):
        metrics.bootstrap_by_article([_record("a", 0)], [], lambda a, b: 1, **kwargs)
