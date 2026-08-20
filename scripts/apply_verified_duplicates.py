"""Aplica na planilha de um revisor:
1. Normalização de digitação nos rótulos já preenchidos (ex.: "Neutro" ->
   "neutro", "ambíguo"/"ambigua" -> "indeterminado") -- mecânico, não é
   julgamento novo, só corrige forma de escrever o mesmo rótulo.
2. Uma lista de pares (linha em branco, linha já respondida) CONFIRMADOS
   MANUALMENTE como a mesma informação -- ver VERIFIED_DUPLICATES abaixo.
   Cada linha preenchida por aqui ganha uma nota em `notes` dizendo de
   onde veio, pra nunca ficar indistinguível de um julgamento humano
   original (rastreabilidade -- context.md raiz, seção 4/13).

Este script NÃO decide sozinho quais pares são duplicados -- isso foi
feito por leitura humana (assistida) de cada candidato encontrado por
scripts/fill_from_similar_answered.py. Adicionar um par novo aqui exige
ler os dois textos primeiro, não só olhar o score de similaridade (ver
docstring de fill_from_similar_answered.py para o porquê).

Uso:
    .venv/bin/python scripts/apply_verified_duplicates.py <arquivo.csv>

Sempre escreve um .bak do arquivo original antes de sobrescrever.
"""
from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

LABEL_ALIASES = {
    "neutro": "neutro", "neutra": "neutro", "neutros": "neutro", "neutras": "neutro",
    "negativo": "negativo", "negativa": "negativo",
    "positivo": "positivo", "positiva": "positivo",
    "indeterminado": "indeterminado", "indeterminada": "indeterminado",
    "ambiguo": "indeterminado", "ambigua": "indeterminado", "ambíguo": "indeterminado", "ambígua": "indeterminado",
    "nao_relacionado": "nao_relacionado", "não_relacionado": "nao_relacionado",
    "não relacionado": "nao_relacionado", "nao relacionado": "nao_relacionado",
}

# (paragraph_id em branco, entity_name, paragraph_id de referência já
# respondida) -- confirmados por leitura manual em 2026-08-20, não só por
# score de similaridade. Ver conversa/commit correspondente para o porquê
# de cada um.
VERIFIED_DUPLICATES: list[tuple[str, str, str]] = [
    ("493-0", "Jair Bolsonaro", "102-6"),   # texto idêntico (100%): "Flávio Bolsonaro chega a comício..."
    ("264-13", "Luiz Inácio Lula da Silva", "9-13"),  # mesma proposta: Ministério da Segurança Pública, condicionado à PEC
    ("68-5", "Luiz Inácio Lula da Silva", "9-13"),    # idem, com detalhe extra (quem enviou a PEC) mas mesmo fato central
    ("43-15", "Marina Silva", "20-3"),      # mesma info: Tebet e Marina Silva como candidatas no mesmo evento
    ("484-8", "Jair Bolsonaro", "2-24"),    # mesma citação de Flávio ("...onde o atual presidente não pode pisar..."), portais diferentes
]


def normalize_label(raw: str) -> str:
    key = raw.strip().lower()
    return LABEL_ALIASES.get(key, raw.strip())


def main() -> None:
    if len(sys.argv) != 2:
        print("uso: apply_verified_duplicates.py <arquivo.csv>")
        sys.exit(1)

    path = Path(sys.argv[1])
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fieldnames = list(rows[0].keys())

    by_key = {(r["paragraph_id"], r["entity_name"]): r for r in rows}

    label_changes = 0
    for row in rows:
        if row["label"].strip():
            normalized = normalize_label(row["label"])
            if normalized != row["label"].strip():
                label_changes += 1
            row["label"] = normalized

    applied = 0
    for blank_id, entity, ref_id in VERIFIED_DUPLICATES:
        blank = by_key.get((blank_id, entity))
        ref = by_key.get((ref_id, entity))
        if blank is None or ref is None:
            print(f"AVISO: par ({blank_id}, {entity}, {ref_id}) não encontrado no arquivo -- pulado")
            continue
        if blank["label"].strip():
            print(f"AVISO: {blank_id} [{entity}] já tinha rótulo ({blank['label']!r}) -- não sobrescrito")
            continue
        blank["label"] = ref["label"]
        if ref["intensity"].strip():
            blank["intensity"] = ref["intensity"]
        note = f"[auto: mesma informação que {ref_id}, confirmado por leitura manual]"
        blank["notes"] = (blank["notes"] + " " + note).strip() if blank["notes"].strip() else note
        applied += 1

    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy(path, backup)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"rótulos normalizados (digitação): {label_changes}")
    print(f"pares de duplicata aplicados: {applied} de {len(VERIFIED_DUPLICATES)}")
    print(f"original preservado em {backup}")


if __name__ == "__main__":
    main()
