"""Lista candidatos a "mesma informação, portal diferente" numa planilha de
revisor parcialmente respondida -- SÓ um relatório para leitura humana, não
aplica nada sozinho.

Por que não auto-aplica: testado contra data/annotation/revisor_bea (166
respondidas, 346 em branco), um threshold de similaridade textual alto
(90%) só achou 1 de verdade, e vários candidatos com score alto (60-66%)
eram "mesmo evento, fato diferente" (ex.: dois parágrafos sobre o mesmo
comício, um fala de quem discursou, outro de quantas pessoas compareceram)
-- copiar o rótulo nesses casos seria inventar um julgamento que a pessoa
não deu. Por outro lado, pares que ERAM a mesma informação de verdade (a
mesma proposta de governo, a mesma citação) apareceram com score bem mais
baixo (58%) por causa de paráfrase real entre portais. Ou seja: nem
threshold alto é seguro, nem baixo é suficiente -- cada candidato precisa
ser lido antes de copiar (ver scripts/apply_verified_duplicates.py para o
padrão de como aplicar só pares confirmados manualmente, com nota de
rastreabilidade).

Uso:
    .venv/bin/python scripts/fill_from_similar_answered.py <arquivo.csv>
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path

from rapidfuzz import fuzz

TOP_N_PER_ENTITY_GROUP = 40  # quantos candidatos mostrar no relatório, não um corte de decisão

LABEL_ALIASES = {
    "neutro": "neutro", "neutra": "neutro", "neutros": "neutro", "neutras": "neutro",
    "negativo": "negativo", "negativa": "negativo",
    "positivo": "positivo", "positiva": "positivo",
    "indeterminado": "indeterminado", "indeterminada": "indeterminado",
    "ambiguo": "indeterminado", "ambigua": "indeterminado", "ambíguo": "indeterminado", "ambígua": "indeterminado",
    "nao_relacionado": "nao_relacionado", "não_relacionado": "nao_relacionado",
    "não relacionado": "nao_relacionado", "nao relacionado": "nao_relacionado",
}


def normalize_label(raw: str) -> str:
    key = raw.strip().lower()
    return LABEL_ALIASES.get(key, raw.strip())


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("uso: fill_from_similar_answered.py <arquivo.csv>")
        sys.exit(1)

    path = Path(args[0])
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    filled_by_entity: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["label"].strip():
            filled_by_entity[row["entity_name"]].append(
                {**row, "label": normalize_label(row["label"])}
            )

    candidates_by_blank = []
    for row in rows:
        if row["label"].strip():
            continue
        best, best_score = None, 0.0
        for cand in filled_by_entity.get(row["entity_name"], []):
            score = fuzz.token_sort_ratio(row["paragraph_text"], cand["paragraph_text"])
            if score > best_score:
                best_score, best = score, cand
        candidates_by_blank.append((best_score, row, best))

    candidates_by_blank.sort(key=lambda t: -t[0])
    print(f"{len(candidates_by_blank)} linhas em branco. Top {TOP_N_PER_ENTITY_GROUP} candidatos por score (LEIA antes de decidir -- score alto não garante mesma informação, score baixo às vezes é a mesma informação parafraseada):\n")
    for score, row, best in candidates_by_blank[:TOP_N_PER_ENTITY_GROUP]:
        best_desc = f"{best['paragraph_id']} (label={best['label']!r}, score={score:.0f}%)" if best else "nenhum candidato"
        print(f"- {row['paragraph_id']} [{row['entity_name']}] <-> {best_desc}")
        print(f"  BLANK: {row['paragraph_text'][:160]}")
        if best:
            print(f"  REF:   {best['paragraph_text'][:160]}")
        print()


if __name__ == "__main__":
    main()
