"""Testes do experiments/gold.py com parágrafos e entidades falsos (sem
depender do CSV real)."""
import json

import pytest
from openpyxl import load_workbook

import gold

ENTITIES = [
    {"entity_id": "1", "nome_principal": "LUIZ INÁCIO LULA DA SILVA"},
    {"entity_id": "2", "nome_principal": "FLÁVIO BOLSONARO"},
]
ARTICLES = {
    "150": {
        "title": "Título de teste",
        "paragraphs": [
            "Lula discursou em São Paulo. Depois, Lula foi ao Rio.",
            "O presidente afirmou que  vai recorrer da decisão.",
            "Parágrafo sem nenhum nome, mas ele fala do candidato.",
        ],
    },
    "2": {"title": "=SOMA(1;1)", "paragraphs": ["Flávio Bolsonaro visitou a feira hoje."]},
}


# --- find_span -------------------------------------------------------------


def test_trecho_exato():
    start, end, aviso = gold.find_span("O presidente afirmou", "O presidente", None)
    assert (start, end, aviso) == (0, 12, None)


def test_ocorrencia_2():
    par = ARTICLES["150"]["paragraphs"][0]
    start, end, _ = gold.find_span(par, "Lula", 2)
    assert par[start:end] == "Lula" and start == par.rfind("Lula")


def test_trecho_ausente():
    with pytest.raises(gold.SpanError, match="não encontrado"):
        gold.find_span("O presidente afirmou", "o governador", None)


def test_trecho_repetido_sem_ocorrencia():
    with pytest.raises(gold.SpanError, match="aparece 2 vezes"):
        gold.find_span(ARTICLES["150"]["paragraphs"][0], "Lula", None)


def test_ocorrencia_maior_que_o_total():
    with pytest.raises(gold.SpanError, match="ocorrencia 3"):
        gold.find_span(ARTICLES["150"]["paragraphs"][0], "Lula", 3)


def test_fallback_maiuscula_e_espaco_devolve_offsets_no_original():
    par = "Ontem O  Presidente afirmou."
    start, end, aviso = gold.find_span(par, "o presidente", None)
    assert par[start:end] == "O  Presidente"
    assert aviso is not None


# --- validate_workbook -----------------------------------------------------


def _filled_workbook(tmp_path, mencoes, teor):
    """Gera a planilha, preenche via openpyxl, salva e relê (ida e volta)."""
    path = tmp_path / "bea.xlsx"
    gold.build_workbook(ARTICLES, ENTITIES).save(path)
    wb = load_workbook(path)
    for row, values in enumerate(mencoes, start=2):
        for col, value in enumerate(values, start=1):
            wb["mencoes"].cell(row=row, column=col).value = value
    for row, values in enumerate(teor, start=2):
        for col, value in enumerate(values, start=1):
            wb["teor"].cell(row=row, column=col).value = value
    wb.save(path)
    return load_workbook(path, data_only=True)


def test_teor_duplicado_e_erro(tmp_path):
    wb = _filled_workbook(
        tmp_path,
        mencoes=[],
        teor=[
            ("150-0", "LUIZ INÁCIO LULA DA SILVA", "positivo"),
            ("150-0", "LUIZ INÁCIO LULA DA SILVA", "negativo"),
        ],
    )
    records, issues = gold.validate_workbook(wb, "bea", ARTICLES, ENTITIES)
    erros = [i for i in issues if i["nivel"] == "erro"]
    assert len(erros) == 1 and erros[0]["linha"] == 3 and "duplicado" in erros[0]["motivo"]
    assert records[0]["teor"] == {"1": "positivo"}


def test_ida_e_volta_planilha_para_jsonl(tmp_path):
    wb = _filled_workbook(
        tmp_path,
        mencoes=[
            ("150-0", "Lula", 2, "DIR", "LUIZ INÁCIO LULA DA SILVA"),
            ("150-1", "o presidente", None, "IND", "LUIZ INÁCIO LULA DA SILVA"),  # aviso: maiúscula
            ("150-2", "governador", None, "IND", "FLÁVIO BOLSONARO"),  # erro: ausente
            ("999-0", "x", None, "DIR", "FLÁVIO BOLSONARO"),  # erro: paragraph_id
            ("2-0", "Flávio Bolsonaro", None, "XXX", "FLÁVIO BOLSONARO"),  # erro: tipo
            ("2-0", "Flávio", None, "DIR", "FLÁVIO BOLSONARO"),  # aviso: sem teor
        ],
        teor=[("150-0", "LUIZ INÁCIO LULA DA SILVA", "positivo"), ("150-1", "LUIZ INÁCIO LULA DA SILVA", "nada")],
    )
    records, issues = gold.validate_workbook(wb, "bea", ARTICLES, ENTITIES)

    # Todos os parágrafos, inclusive os sem anotação, na ordem.
    assert [(r["article_id"], r["paragraph_index"]) for r in records] == [("150", 0), ("150", 1), ("150", 2), ("2", 0)]
    for r in records:
        for m in r["mentions"]:
            assert r["text"][m["start"] : m["end"]] == m["text"]
        json.dumps(r, ensure_ascii=False)

    assert records[0]["mentions"] == [{"start": 37, "end": 41, "text": "Lula", "type": "DIR", "entity_id": "1"}]
    assert records[0]["teor"] == {"1": "positivo"}
    assert records[1]["mentions"][0]["text"] == "O presidente"
    assert records[1]["teor"] == {}  # teor "nada" é erro e fica fora
    assert records[2]["mentions"] == []
    assert [m["text"] for m in records[3]["mentions"]] == ["Flávio"]
    assert records[0]["annotator"] == "bea" and records[0]["split"] is None

    erros = {(i["aba"], i["linha"]) for i in issues if i["nivel"] == "erro"}
    assert erros == {("mencoes", 4), ("mencoes", 5), ("mencoes", 6), ("teor", 3)}
    avisos = {(i["aba"], i["linha"]) for i in issues if i["nivel"] == "aviso"}
    # linha 3: maiúscula; linha 3 de novo: sem teor em 150-1; linha 7: sem teor em 2-0
    assert avisos == {("mencoes", 3), ("mencoes", 7)}


def test_planilha_tem_todos_os_paragrafos_e_titulo_nao_vira_formula(tmp_path):
    path = tmp_path / "a.xlsx"
    gold.build_workbook(ARTICLES, ENTITIES).save(path)
    wb = load_workbook(path)
    rows = list(wb["paragrafos"].iter_rows(min_row=2, values_only=True))
    assert [r[0] for r in rows] == ["150-0", "150-1", "150-2", "2-0"]
    assert rows[-1][3] == "=SOMA(1;1)" and wb["paragrafos"]["D5"].data_type == "s"
    assert wb["listas"].sheet_state == "hidden"


def test_trechos_sobrepostos_geram_aviso(tmp_path):
    wb = _filled_workbook(
        tmp_path,
        mencoes=[
            ("2-0", "Flávio Bolsonaro", None, "DIR", "FLÁVIO BOLSONARO"),
            ("2-0", "Bolsonaro", None, "DIR", "FLÁVIO BOLSONARO"),
        ],
        teor=[("2-0", "FLÁVIO BOLSONARO", "neutro")],
    )
    _, issues = gold.validate_workbook(wb, "bea", ARTICLES, ENTITIES)
    assert [(i["nivel"], i["linha"]) for i in issues] == [("aviso", 3)]
    assert "sobreposto" in issues[0]["motivo"]


# --- split -----------------------------------------------------------------


def test_split_deterministico_disjunto_e_compartilhados_no_teste():
    ids = [str(i) for i in range(1, 61)]
    shared = ["3", "10", "20", "30", "40", "50"]
    a, warnings = gold.split_articles(ids, shared, seed=42)
    b, _ = gold.split_articles(list(reversed(ids)), shared, seed=42)
    assert a == b  # ordem de entrada não importa
    assert warnings == []
    assert set(a) == set(ids)  # cada artigo em exatamente um split (dict)
    assert all(a[s] == "test" for s in shared)
    counts = {s: list(a.values()).count(s) for s in ("train", "validation", "test")}
    assert counts == {"train": 39, "validation": 6, "test": 15}


def test_split_compartilhados_acima_da_cota_ficam_no_teste_com_aviso():
    ids = [str(i) for i in range(1, 9)]  # cota de teste = 2
    a, warnings = gold.split_articles(ids, ["1", "2", "3"], seed=42)
    assert all(a[s] == "test" for s in ["1", "2", "3"])
    assert list(a.values()).count("test") == 3
    assert len(warnings) == 1


# --- teor: lista de valores --------------------------------------------------


def test_teor_aceita_indeterminado_e_recusa_nao_relacionado(tmp_path):
    wb = _filled_workbook(
        tmp_path,
        mencoes=[],
        teor=[
            ("150-0", "LUIZ INÁCIO LULA DA SILVA", "indeterminado"),
            ("2-0", "FLÁVIO BOLSONARO", "nao_relacionado"),
        ],
    )
    records, issues = gold.validate_workbook(wb, "bea", ARTICLES, ENTITIES)
    assert records[0]["teor"] == {"1": "indeterminado"}
    assert records[3]["teor"] == {}
    assert [(i["nivel"], i["linha"]) for i in issues] == [("erro", 3)]
    formulas = [dv.formula1 for dv in wb["teor"].data_validations.dataValidation]
    assert '"positivo,negativo,neutro,indeterminado"' in formulas


# --- tempo -------------------------------------------------------------------


def test_aba_tempo_pre_preenchida_na_ordem_dos_artigos(tmp_path):
    path = tmp_path / "a.xlsx"
    gold.build_workbook(ARTICLES, ENTITIES).save(path)
    wb = load_workbook(path)
    rows = list(wb["tempo"].iter_rows(min_row=2, values_only=True))
    assert [r[:3] for r in rows] == [("150", "Título de teste", None), ("2", "=SOMA(1;1)", None)]
    dv = wb["tempo"].data_validations.dataValidation[0]
    assert (dv.type, dv.formula1, dv.formula2, str(dv.sqref)) == ("whole", "1", "600", "C2:C3")


def test_validar_tempo_vazio_e_aviso_e_texto_e_erro(tmp_path):
    path = tmp_path / "bea.xlsx"
    gold.build_workbook(ARTICLES, ENTITIES).save(path)
    wb = load_workbook(path)
    wb["tempo"]["C2"] = 25
    wb.save(path)
    rows, issues = gold.validate_tempo(load_workbook(path, data_only=True), "bea", ARTICLES)
    assert rows == [
        {"anotador": "bea", "article_id": "150", "n_paragrafos": 3, "minutos": 25},
        {"anotador": "bea", "article_id": "2", "n_paragrafos": 1, "minutos": None},
    ]
    assert [(i["nivel"], i["linha"]) for i in issues] == [("aviso", 3)]

    wb = load_workbook(path)
    wb["tempo"]["C2"] = "meia hora"
    wb["tempo"]["C3"] = 900
    wb.save(path)
    rows, issues = gold.validate_tempo(load_workbook(path, data_only=True), "bea", ARTICLES)
    assert [r["minutos"] for r in rows] == [None, None]
    assert [(i["nivel"], i["linha"]) for i in issues] == [("erro", 2), ("erro", 3)]
