"""Junta as planilhas devolvidas pelos 6 revisores (data/annotation/revisor_*.csv)
de volta usando manifest.csv como chave, mede concordância entre anotadores
nas linhas de sobreposição (Fleiss' kappa) e gera o dataset consolidado.

Filosofia (context.md raiz, seção 15): divergência entre anotadores deve
ser REGISTRADA, não escondida atrás de uma média/maioria silenciosa. Por
isso uma unidade de sobreposição só ganha `final_label` automático quando
os revisores que responderam concordam; discordância vira
`PENDENTE_REVISAO` em vez de decidida por voto de maioria sem revisão
humana.

Uso:
    .venv/bin/python scripts/reconcile_annotations.py

Lê data/annotation/manifest.csv + revisor_*.csv, escreve:
- data/annotation/consolidated.csv -- uma linha por unidade, com
  final_label pronto (ou PENDENTE_REVISAO) e intensity mediana quando há
  mais de uma resposta.
- data/annotation/agreement_report.md -- kappa geral e por par, contagem de
  discordâncias, e a lista concreta das unidades em disputa (com o texto,
  pra facilitar a revisão).
"""
from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "annotation"
MANIFEST_PATH = DATA_DIR / "manifest.csv"
REVIEWERS = [f"revisor_{i}" for i in range(1, 7)]

VALID_LABELS = {"neutro", "negativo", "positivo", "indeterminado", "nao_relacionado"}
# Tolerância a variação de digitação humana (acento, maiúscula, espaço) --
# normaliza pro valor canônico em vez de rejeitar de cara.
_LABEL_ALIASES = {
    "não_relacionado": "nao_relacionado",
    "não relacionado": "nao_relacionado",
    "nao relacionado": "nao_relacionado",
}


@dataclass
class Response:
    reviewer: str
    label: str | None
    intensity: int | None
    notes: str


@dataclass
class Unit:
    unit_id: str
    article_id: str
    paragraph_id: str
    entity_id: str
    entity_name: str
    split: str
    is_overlap: bool
    paragraph_text: str
    expected_reviewers: list[str]
    responses: list[Response] = field(default_factory=list)


def normalize_label(raw: str) -> str | None:
    value = raw.strip().lower()
    if not value:
        return None
    value = _LABEL_ALIASES.get(value, value)
    return value if value in VALID_LABELS else "INVALIDO:" + raw.strip()


def load_manifest() -> dict[tuple[str, str], Unit]:
    units: dict[tuple[str, str], Unit] = {}
    with open(MANIFEST_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (row["paragraph_id"], row["entity_name"])
            units[key] = Unit(
                unit_id=row["unit_id"],
                article_id=row["article_id"],
                paragraph_id=row["paragraph_id"],
                entity_id=row["entity_id"],
                entity_name=row["entity_name"],
                split=row["split"],
                is_overlap=row["is_overlap"] == "True",
                paragraph_text=row["paragraph_text"],
                expected_reviewers=row["reviewers"].split(";"),
            )
    return units


def load_responses(units: dict[tuple[str, str], Unit]) -> list[str]:
    """Preenche unit.responses lendo cada revisor_N.csv. Retorna avisos
    (linhas que não bateram com nenhuma unidade do manifest -- paragraph_id
    ou entity_name alterado sem querer, por exemplo)."""
    warnings: list[str] = []
    for reviewer in REVIEWERS:
        path = DATA_DIR / f"{reviewer}.csv"
        if not path.exists():
            warnings.append(f"{reviewer}.csv não encontrado -- pulando (ainda não devolvido?)")
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                raw_label = (row.get("label") or "").strip()
                if not raw_label:
                    continue  # linha ainda não respondida -- não conta como resposta
                key = (row["paragraph_id"], row["entity_name"])
                unit = units.get(key)
                if unit is None:
                    warnings.append(
                        f"{reviewer}: linha (paragraph_id={row['paragraph_id']!r}, "
                        f"entity_name={row['entity_name']!r}) não existe no manifest -- ignorada"
                    )
                    continue
                raw_intensity = (row.get("intensity") or "").strip()
                intensity = int(raw_intensity) if raw_intensity.isdigit() else None
                unit.responses.append(
                    Response(
                        reviewer=reviewer,
                        label=normalize_label(raw_label),
                        intensity=intensity,
                        notes=(row.get("notes") or "").strip(),
                    )
                )
    return warnings


def fleiss_kappa(rows: list[Counter]) -> float | None:
    """Fleiss' kappa clássico para N sujeitos avaliados por (até) k
    avaliadores cada, sobre um conjunto fixo de categorias. `rows` é uma
    Counter por unidade (categoria -> nº de avaliadores que escolheram
    ela). Unidades com menos de 2 respostas são ignoradas (não dá pra medir
    concordância com 1 voto só). Retorna None se não sobrar unidade
    suficiente."""
    usable = [r for r in rows if sum(r.values()) >= 2]
    if not usable:
        return None

    categories = sorted({cat for r in usable for cat in r})
    n_units = len(usable)

    p_j = {cat: 0.0 for cat in categories}
    p_i = []
    for r in usable:
        n_i = sum(r.values())
        p_i.append((sum(count * (count - 1) for count in r.values())) / (n_i * (n_i - 1)))
        for cat in categories:
            p_j[cat] += r.get(cat, 0)

    total_ratings = sum(sum(r.values()) for r in usable)
    for cat in p_j:
        p_j[cat] /= total_ratings

    p_bar = sum(p_i) / n_units
    p_e_bar = sum(p ** 2 for p in p_j.values())

    if p_e_bar == 1.0:
        return 1.0 if p_bar == 1.0 else 0.0
    return (p_bar - p_e_bar) / (1 - p_e_bar)


def resolve_final_label(unit: Unit) -> tuple[str, bool]:
    """(final_label, precisa_revisao). Sem resposta -> PENDENTE_RESPOSTA.
    Uma resposta -> usa ela direto. Mais de uma -> só resolve automático se
    todo mundo concordou; discordância nunca é decidida por maioria
    silenciosa aqui (context.md raiz, seção 15)."""
    labels = [r.label for r in unit.responses if r.label]
    if not labels:
        return "PENDENTE_RESPOSTA", True
    distinct = set(labels)
    if len(distinct) == 1:
        return labels[0], False
    return "PENDENTE_REVISAO", True


def write_consolidated(units: list[Unit]) -> None:
    path = DATA_DIR / "consolidated.csv"
    fieldnames = [
        "unit_id", "article_id", "paragraph_id", "entity_id", "entity_name", "split",
        "is_overlap", "n_responses", "final_label", "needs_review", "median_intensity",
        "paragraph_text",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for u in units:
            final_label, needs_review = resolve_final_label(u)
            intensities = [r.intensity for r in u.responses if r.intensity is not None]
            writer.writerow(
                {
                    "unit_id": u.unit_id,
                    "article_id": u.article_id,
                    "paragraph_id": u.paragraph_id,
                    "entity_id": u.entity_id,
                    "entity_name": u.entity_name,
                    "split": u.split,
                    "is_overlap": u.is_overlap,
                    "n_responses": len(u.responses),
                    "final_label": final_label,
                    "needs_review": needs_review,
                    "median_intensity": statistics.median(intensities) if intensities else "",
                    "paragraph_text": u.paragraph_text,
                }
            )
    print(f"escrito {path}")


def write_agreement_report(units: list[Unit], warnings: list[str]) -> None:
    overlap_units = [u for u in units if u.is_overlap]
    answered_overlap = [u for u in overlap_units if u.responses]

    rows_for_kappa = [Counter(r.label for r in u.responses if r.label) for u in answered_overlap]
    kappa = fleiss_kappa(rows_for_kappa)

    disagreements = [u for u in answered_overlap if resolve_final_label(u)[0] == "PENDENTE_REVISAO"]
    fully_answered = [u for u in overlap_units if len(u.responses) == len(REVIEWERS)]

    lines = ["# Relatório de concordância entre anotadores", ""]
    lines.append(f"- Unidades de sobreposição no total: {len(overlap_units)}")
    lines.append(f"- Com pelo menos 1 resposta: {len(answered_overlap)}")
    lines.append(f"- Respondidas pelos 6 revisores: {len(fully_answered)}")
    lines.append(f"- Fleiss' kappa (unidades com 2+ respostas): {kappa:.3f}" if kappa is not None else "- Fleiss' kappa: sem dado suficiente ainda")
    lines.append(f"- Unidades em discordância (PENDENTE_REVISAO): {len(disagreements)}")
    lines.append("")
    lines.append(
        "Referência de leitura do kappa (Landis & Koch): <0 sem concordância, "
        "0-0.20 leve, 0.21-0.40 razoável, 0.41-0.60 moderada, 0.61-0.80 substancial, 0.81-1.00 quase perfeita."
    )
    lines.append("")

    if disagreements:
        lines.append("## Unidades em discordância (revisar manualmente)")
        lines.append("")
        for u in disagreements:
            votes = ", ".join(f"{r.reviewer}={r.label}" for r in u.responses)
            lines.append(f"- `{u.unit_id}` ({u.entity_name}): {votes}")
            lines.append(f"  > {u.paragraph_text[:200]}")
        lines.append("")

    if warnings:
        lines.append("## Avisos ao processar as planilhas")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    path = DATA_DIR / "agreement_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"escrito {path}")


def main() -> None:
    units_by_key = load_manifest()
    warnings = load_responses(units_by_key)
    units = list(units_by_key.values())

    answered = sum(1 for u in units if u.responses)
    print(f"{answered} de {len(units)} unidades com pelo menos 1 resposta")

    write_consolidated(units)
    write_agreement_report(units, warnings)


if __name__ == "__main__":
    main()
