"""Testes do experiments/consolidate.py com dados sintéticos (sem depender
das planilhas reais do piloto)."""
import hashlib
import json

import pytest
import yaml
from openpyxl import load_workbook

import consolidate
import gold

TEXT = "Lula falou com Flávio Bolsonaro. O partido reagiu. Ele saiu."
VOTERS = ["A", "B", "C", "D", "E"]
TIE = {"A": {"1": "positivo"}, "B": {"1": "positivo"}, "C": {"1": "negativo"}, "D": {"1": "negativo"}, "E": {"1": "neutro"}}
DECISION_COL = consolidate.PENDENCIAS_COLS.index("decisao") + 1


def _m(trecho, entity, tipo="DIR"):
    start = TEXT.index(trecho)
    return {"start": start, "end": start + len(trecho), "text": trecho, "type": tipo, "entity_id": entity}


def _annotators(mentions=None, teor=None):
    mentions, teor = mentions or {}, teor or {}
    return {
        v: [{"article_id": "10", "paragraph_index": 0, "text": TEXT, "split": None,
             "mentions": mentions.get(v, []), "teor": teor.get(v, {}), "annotator": v}]
        for v in VOTERS
    }


# --- menções -----------------------------------------------------------------


def test_mencao_marcada_por_3_de_5_entra_na_referencia():
    lula = _m("Lula", "1")
    result = consolidate.consolidate(_annotators(mentions={v: [lula] for v in "ABC"}))
    assert result["records"][0]["mentions"] == [lula]
    assert result["contagens"]["mencoes_aceitas_por_votos"] == {"5": 0, "4": 0, "3": 1}
    assert result["pendencias"] == []


def test_mencao_marcada_por_2_de_5_nao_entra_e_e_listada_como_descarte():
    lula = _m("Lula", "1")
    result = consolidate.consolidate(_annotators(mentions={v: [lula] for v in "AB"}))
    assert result["records"][0]["mentions"] == []
    assert result["pendencias"] == []
    [descarte] = result["descartes"]["mencoes"]
    assert descarte["votantes"] == ["A", "B"] and descarte["motivo"] == "sem maioria, descartado"


def test_tipo_segue_maioria_de_quem_marcou_e_empate_vira_pendencia():
    direta, indireta = _m("Ele", "1", "DIR"), _m("Ele", "1", "IND")
    result = consolidate.consolidate(_annotators(mentions={"A": [indireta], "B": [indireta], "C": [direta]}))
    assert result["records"][0]["mentions"] == [indireta]

    result = consolidate.consolidate(_annotators(mentions={"A": [indireta], "B": [indireta], "C": [direta], "D": [direta]}))
    assert result["records"][0]["mentions"] == []
    [pendencia] = result["pendencias"]
    assert pendencia["tipo"] == "mencao_tipo"
    assert [(o["type"], o["votantes"]) for o in pendencia["opcoes"]] == [("DIR", ["C", "D"]), ("IND", ["A", "B"])]


def test_grupo_sobreposto_sem_trecho_majoritario_vira_pendencia():
    longo, curto = _m("Flávio Bolsonaro", "2"), _m("Flávio", "2")
    result = consolidate.consolidate(_annotators(mentions={"A": [longo], "B": [longo], "C": [curto]}))
    assert result["records"][0]["mentions"] == []
    [pendencia] = result["pendencias"]
    assert (pendencia["id"], pendencia["tipo"]) == ("M001", "mencao_limites")
    assert [(o["n"], o["text"], o["votantes"]) for o in pendencia["opcoes"]] == [
        (1, "Flávio", ["C"]), (2, "Flávio Bolsonaro", ["A", "B"]),
    ]
    assert pendencia["decisoes_validas"] == ["1", "2", "fora"]


def test_sobreposicao_com_entidades_diferentes_somando_3_vira_pendencia():
    ele_lula, ele_flavio = _m("Ele", "1", "IND"), _m("Ele", "2", "IND")
    result = consolidate.consolidate(_annotators(mentions={"A": [ele_lula], "B": [ele_lula], "C": [ele_flavio]}))
    assert [p["tipo"] for p in result["pendencias"]] == ["mencao_entidades"]


def test_grupo_sobreposto_com_menos_de_3_votantes_e_descartado():
    longo, curto = _m("Flávio Bolsonaro", "2"), _m("Flávio", "2")
    result = consolidate.consolidate(_annotators(mentions={"A": [longo], "B": [curto]}))
    assert result["pendencias"] == []
    assert [d["text"] for d in result["descartes"]["mencoes"]] == ["Flávio", "Flávio Bolsonaro"]


def test_referencia_generica_de_partido_nunca_entra():
    generica = _m("O partido", "3", "IND")
    result = consolidate.consolidate(_annotators(mentions={v: [generica] for v in VOTERS}))
    assert result["records"][0]["mentions"] == []
    assert result["pendencias"] == []
    assert [i["votante"] for i in result["exclusoes"]["d15_partido_generico"]] == VOTERS


@pytest.mark.parametrize("texto", ["o partido", "O Partido", "a legenda", "das siglas", "pelo  partido", "à legenda", "partidos"])
def test_formas_genericas_de_partido(texto):
    assert consolidate.is_generic_party(texto)


@pytest.mark.parametrize("texto", ["Partido Liberal", "o Partido dos Trabalhadores", "PT", "o petista"])
def test_nomes_de_partido_nao_sao_genericos(texto):
    assert not consolidate.is_generic_party(texto)


def test_trecho_de_paragrafo_inteiro_nao_conta_como_voto():
    inteiro = {"start": 0, "end": len(TEXT), "text": TEXT, "type": "DIR", "entity_id": "1"}
    result = consolidate.consolidate(_annotators(mentions={v: [inteiro] for v in VOTERS}))
    assert result["records"][0]["mentions"] == []
    assert len(result["exclusoes"]["paragrafo_inteiro"]) == 5


def test_mencao_sem_teor_gera_alerta_sem_resolver():
    lula = _m("Lula", "1")
    result = consolidate.consolidate(_annotators(mentions={v: [lula] for v in "ABC"}))
    assert result["alertas"]["mencao_sem_teor"] == [{"paragraph_id": "10-0", "entity_id": "1"}]
    assert result["records"][0]["teor"] == {}


# --- teor --------------------------------------------------------------------


def test_empate_de_teor_vira_pendencia():
    result = consolidate.consolidate(_annotators(teor=TIE))
    assert result["records"][0]["teor"] == {}
    [pendencia] = result["pendencias"]
    assert (pendencia["id"], pendencia["tipo"], pendencia["entity_id"]) == ("T001", "teor_empate", "1")
    assert {o["label"]: o["votantes"] for o in pendencia["opcoes"]} == {
        "positivo": ["A", "B"], "negativo": ["C", "D"], "neutro": ["E"],
    }


def test_ausencia_de_rotulo_nao_conta_como_neutro():
    # Dois neutros e três ausências: não entra nem vira pendência.
    result = consolidate.consolidate(_annotators(teor={"A": {"1": "neutro"}, "B": {"1": "neutro"}}))
    assert result["records"][0]["teor"] == {}
    assert result["pendencias"] == []
    assert result["descartes"]["teor"][0]["sem_rotulo"] == ["C", "D", "E"]

    # Três rotularam (2 neutros, 1 positivo): os ausentes não completam o neutro.
    result = consolidate.consolidate(_annotators(teor={"A": {"1": "neutro"}, "B": {"1": "neutro"}, "C": {"1": "positivo"}}))
    assert result["records"][0]["teor"] == {}
    assert [p["tipo"] for p in result["pendencias"]] == ["teor_empate"]


def test_teor_com_3_votos_entra_inclusive_indeterminado():
    result = consolidate.consolidate(_annotators(teor={v: {"1": "indeterminado"} for v in "ABC"}))
    assert result["records"][0]["teor"] == {"1": "indeterminado"}
    assert result["contagens"]["teor_aceito_por_votos"]["3"] == 1


# --- aplicar -----------------------------------------------------------------


def _pendencias_file(tmp_path, result, decisoes):
    path = tmp_path / "pendencias.xlsx"
    consolidate.build_pendencias_workbook(result["pendencias"], {"10-0": TEXT}, {}).save(path)
    wb = load_workbook(path)
    for row, value in enumerate(decisoes, start=2):
        wb["pendencias"].cell(row=row, column=DECISION_COL).value = value
    wb.save(path)
    return path


@pytest.mark.parametrize("valor, motivo", [(None, "vazia"), ("talvez", "inválida")])
def test_aplicar_recusa_decisao_vazia_ou_invalida_com_numero_da_linha(tmp_path, valor, motivo):
    result = consolidate.consolidate(_annotators(teor=TIE))
    path = _pendencias_file(tmp_path, result, [valor])
    with pytest.raises(ValueError, match=rf"linha 2 \(T001\): decisão {motivo}"):
        consolidate.read_decisions(path, result["pendencias"])


def test_aplicar_recusa_opcao_de_mencao_inexistente(tmp_path):
    longo, curto = _m("Flávio Bolsonaro", "2"), _m("Flávio", "2")
    result = consolidate.consolidate(_annotators(mentions={"A": [longo], "B": [longo], "C": [curto]}))
    path = _pendencias_file(tmp_path, result, ["9"])
    with pytest.raises(ValueError, match=r"linha 2 \(M001\): .*opção inexistente"):
        consolidate.read_decisions(path, result["pendencias"])


def test_aplicar_inclui_escolhas_de_bea_sem_alterar_a_provisoria(tmp_path):
    longo, curto = _m("Flávio Bolsonaro", "2"), _m("Flávio", "2")
    result = consolidate.consolidate(_annotators(mentions={"A": [longo], "B": [longo], "C": [curto]}, teor=TIE))
    assert [p["id"] for p in result["pendencias"]] == ["M001", "T001"]
    path = _pendencias_file(tmp_path, result, ["2", "Negativo"])
    decisions = consolidate.read_decisions(path, result["pendencias"])
    records, log = consolidate.apply_decisions(result["records"], result["pendencias"], decisions)
    assert records[0]["mentions"] == [longo]
    assert records[0]["teor"] == {"1": "negativo"}
    assert [entry["linha_planilha"] for entry in log] == [2, 3]
    assert result["records"][0]["mentions"] == [] and result["records"][0]["teor"] == {}


# --- arquivos: somente leitura e recusa de sobrescrever --------------------------


ENTITIES = [{"entity_id": "1", "nome_principal": "LULA"}, {"entity_id": "2", "nome_principal": "FLÁVIO"}]
ARTICLES = {"10": {"title": "t", "paragraphs": [TEXT]}}


def _sources(tmp_path, monkeypatch):
    """Cinco planilhas sintéticas geradas por gold.build_workbook. O CSV é
    fictício: load_articles devolve ARTICLES, então a segmentação de
    produção não entra no teste."""
    monkeypatch.setattr(gold, "load_articles", lambda path, ids: ARTICLES)
    entities = tmp_path / "entidades.yaml"
    entities.write_text(yaml.safe_dump({"entities": ENTITIES}, allow_unicode=True), encoding="utf-8")
    ids = tmp_path / "ids.txt"
    ids.write_text("10\n", encoding="utf-8")
    csv_path = tmp_path / "artigos.csv"
    csv_path.write_text("id\n", encoding="utf-8")
    sources = {}
    for voter in VOTERS:
        path = tmp_path / f"{voter}.xlsx"
        gold.build_workbook(ARTICLES, ENTITIES).save(path)
        wb = load_workbook(path)
        for col, value in enumerate(("10-0", "Lula", None, "DIR", "LULA"), start=1):
            wb["mencoes"].cell(row=2, column=col).value = value
        for col, value in enumerate(("10-0", "LULA", TIE[voter]["1"]), start=1):
            wb["teor"].cell(row=2, column=col).value = value
        wb.save(path)
        sources[voter] = path
    return {"sources": sources, "csv_path": csv_path, "entities_path": entities, "ids_path": ids}


def test_consolidar_nao_altera_entradas_e_recusa_sobrescrever(tmp_path, monkeypatch):
    kwargs = _sources(tmp_path, monkeypatch)
    inputs = [*kwargs["sources"].values(), kwargs["csv_path"], kwargs["entities_path"], kwargs["ids_path"]]
    before = {path: path.read_bytes() for path in inputs}

    report, records = consolidate.build_report(**kwargs)
    out = tmp_path / "saida"
    consolidate.write_consolidation(report, records, out)

    assert {path: path.read_bytes() for path in inputs} == before
    assert report["inputs_unchanged"] is True
    assert report["inputs"]["A"]["sha256"] == hashlib.sha256(before[kwargs["sources"]["A"]]).hexdigest()
    assert sorted(p.name for p in out.iterdir()) == sorted(consolidate.CONSOLIDATE_OUTPUTS)
    assert records[0]["mentions"][0]["text"] == "Lula" and records[0]["teor"] == {}
    assert [p["id"] for p in report["pendencias"]] == ["T001"]

    first = (out / "report.json").read_bytes()
    with pytest.raises(ValueError, match="já existe"):
        consolidate.write_consolidation(report, records, out)
    assert (out / "report.json").read_bytes() == first


def test_aplicar_gera_referencia_final_e_recusa_sobrescrever(tmp_path, monkeypatch):
    kwargs = _sources(tmp_path, monkeypatch)
    out = tmp_path / "saida"
    consolidate.write_consolidation(*consolidate.build_report(**kwargs), out)
    pendencias = out / "pendencias.xlsx"
    wb = load_workbook(pendencias)
    wb["pendencias"].cell(row=2, column=DECISION_COL).value = "neutro"
    wb.save(pendencias)

    final = tmp_path / "final"
    argv = ["aplicar", "--consolidacao", str(out), "--saida", str(final)]
    assert consolidate.main(argv) == 0
    [record] = [json.loads(line) for line in (final / "referencia.jsonl").read_text(encoding="utf-8").splitlines()]
    assert record["teor"] == {"1": "neutro"}
    assert [m["text"] for m in record["mentions"]] == ["Lula"]
    log = json.loads((final / "decisoes-desempate.json").read_text(encoding="utf-8"))
    assert [d["id"] for d in log["decisoes"]] == ["T001"]

    first = (final / "referencia.jsonl").read_bytes()
    assert consolidate.main(argv) == 2
    assert (final / "referencia.jsonl").read_bytes() == first


def _aplicado(tmp_path, monkeypatch):
    """Consolidação sintética com T001 decidido como neutro: a referência
    final tem a menção de Lula e o teor 10-0 / 1 = neutro."""
    kwargs = _sources(tmp_path, monkeypatch)
    out = tmp_path / "saida"
    consolidate.write_consolidation(*consolidate.build_report(**kwargs), out)
    wb = load_workbook(out / "pendencias.xlsx")
    wb["pendencias"].cell(row=2, column=DECISION_COL).value = "neutro"
    wb.save(out / "pendencias.xlsx")
    return ["aplicar", "--consolidacao", str(out), "--saida", str(tmp_path / "final")]


def test_aplicar_remove_teor_por_decisao_de_bea_e_registra(tmp_path, monkeypatch):
    argv = _aplicado(tmp_path, monkeypatch)
    argv += ["--remover-teor", "10-0:1", "--motivo-remocao", "D15: só referência genérica"]
    assert consolidate.main(argv) == 0
    final = tmp_path / "final"
    [record] = [json.loads(line) for line in (final / "referencia.jsonl").read_text(encoding="utf-8").splitlines()]
    assert record["teor"] == {}
    assert [m["text"] for m in record["mentions"]] == ["Lula"]
    log = json.loads((final / "decisoes-desempate.json").read_text(encoding="utf-8"))
    assert log["remocoes_teor"] == [{"paragraph_id": "10-0", "entity_id": "1", "teor": "neutro", "motivo": "D15: só referência genérica"}]
    assert log["contagens"]["remocoes_teor"] == 1 and log["contagens"]["pares_de_teor"] == 0
    # Alertas recalculados depois da remoção: a menção de Lula ficou sem teor.
    assert log["alertas"]["mencao_sem_teor"] == [{"paragraph_id": "10-0", "entity_id": "1"}]


@pytest.mark.parametrize("extra, erro", [
    (["--remover-teor", "10-0:2", "--motivo-remocao", "x"], "entidade 2 sem teor no parágrafo 10-0"),
    (["--remover-teor", "99-0:1", "--motivo-remocao", "x"], "parágrafo 99-0 ausente"),
    (["--remover-teor", "10-0"], "--motivo-remocao é obrigatório"),
    (["--remover-teor", "10-0:1", "--motivo-remocao", "  "], "--motivo-remocao é obrigatório"),
    (["--remover-teor", "10-0", "--motivo-remocao", "x"], "use PARAGRAPH_ID:ENTITY_ID"),
])
def test_aplicar_recusa_remocao_de_teor_invalida_sem_gravar(tmp_path, monkeypatch, capsys, extra, erro):
    argv = _aplicado(tmp_path, monkeypatch) + extra
    assert consolidate.main(argv) == 2
    assert erro in capsys.readouterr().err
    assert not (tmp_path / "final").exists()
