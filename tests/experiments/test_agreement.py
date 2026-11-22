"""Casos pequenos para cobertura de concordância e rastreabilidade."""

import agreement


def _record(index, teor):
    return {"article_id": "a", "paragraph_index": index, "mentions": [], "teor": teor}


def test_kappa_inclui_unidades_completas_e_exclui_ausencia_sem_neutro():
    annotators = {
        "A": [
            _record(0, {"x": "positivo", "y": "indeterminado"}),
            _record(1, {"x": "neutro"}),
        ],
        "B": [
            _record(0, {"x": "positivo"}),
            _record(1, {"x": "positivo"}),
        ],
    }

    result = agreement.compute_teor_agreement(annotators)

    assert result["labels"] == ["positivo", "negativo", "neutro", "indeterminado"]
    assert result["union_units"] == 3
    assert result["included_units"] == 2
    assert result["excluded_units"] == 1
    assert result["counts"] == [[2, 0, 0, 0], [1, 0, 1, 0]]
    assert result["excluded"][0]["entity_id"] == "y"
    assert result["excluded"][0]["missing_annotators"] == ["B"]
    assert result["missing_by_annotator"] == {"A": 0, "B": 1}


def test_f1_pareado_declara_direcao_e_preserva_contagem_um_para_um():
    annotators = {
        "A": [_record(0, {}) | {"mentions": [
            {"start": 0, "end": 4, "type": "DIR", "entity_id": "x"},
        ]}],
        "B": [_record(0, {}) | {"mentions": [
            {"start": 0, "end": 4, "type": "DIR", "entity_id": "x"},
            {"start": 0, "end": 4, "type": "DIR", "entity_id": "x"},
        ]}],
    }

    pair = agreement.pairwise_mentions(annotators, n_resamples=5, seed=11)[0]

    assert (pair["reference"], pair["predicted"]) == ("A", "B")
    assert pair["exact"]["overall"]["tp"] == 1
    assert pair["exact"]["overall"]["fp"] == 1
    assert pair["bootstrap"]["exact_overall"]["seed"] == 11
