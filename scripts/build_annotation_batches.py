"""Gera o dataset de anotação (treino/validação/teste + lotes por revisor)
a partir do CSV bruto de artigos do scraper.

Reaproveita a segmentação e o matching de entidade REAIS do pipeline
(src/segmentation.py, src/entity_matcher.py, config/entities.yaml,
config/settings.yaml) -- as unidades geradas aqui são exatamente as que o
Classifier produziria em produção, garantindo que o dataset de anotação
reflete o que o modelo vai realmente ver.

Método (documentado em detalhe em data/annotation/README.md):
- Split treino/validação/teste (70/15/15) no nível de ARTIGO, não de
  parágrafo -- evita vazar parágrafos do mesmo artigo entre splits
  diferentes.
- Dentro de cada split, uma fração (OVERLAP_FRACTION) das unidades é
  atribuída a TODOS os revisores (sobreposição para medir concordância
  entre anotadores -- context.md raiz, seção 15). O restante é dividido
  em round-robin, disjunto, entre os revisores.
- Split (train/val/test) não aparece nas planilhas dos revisores --
  irrelevante para a tarefa deles e evita qualquer viés involuntário.

Determinístico via SEED -- rodar de novo com o mesmo CSV de entrada
produz exatamente a mesma divisão.
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "src"))

import yaml  # noqa: E402

from entities_store import load_and_validate  # noqa: E402
from entity_matcher import match_entities  # noqa: E402
from segmentation import segment  # noqa: E402

RAW_CSV = BASE_DIR / "data" / "raw" / "articles_202608191040.csv"
OUT_DIR = BASE_DIR / "data" / "annotation"

SEED = 42
SPLIT_RATIOS = {"train": 0.70, "validation": 0.15, "test": 0.15}
OVERLAP_FRACTION = 0.10
REVIEWERS = [f"revisor_{i}" for i in range(1, 7)]

# Rodada 1: só uma amostra dos artigos elegíveis, não o corpus inteiro --
# decisão da Bea depois de ver que a granularidade correta (parágrafo real,
# não artigo inteiro) triplicava o volume por revisor. O restante fica
# RESERVADO pra uma rodada 2 (ver `reserved` em build_round()) -- não
# precisa re-sortear do zero, é a mesma ordem embaralhada (SEED=42),
# só uma fatia maior do mesmo prefixo.
ROUND1_ARTICLE_COUNT = 150


def load_units() -> list[dict]:
    entities = load_and_validate(BASE_DIR / "config" / "entities.yaml")
    settings = yaml.safe_load((BASE_DIR / "config" / "settings.yaml").read_text(encoding="utf-8"))
    fuzzy_threshold = settings["entity_matching"]["fuzzy_threshold"]
    entity_names = {e.id: e.canonical_name for e in entities}

    with open(RAW_CSV, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["extraction_status"] == "success"]

    units: list[dict] = []
    for row in rows:
        paragraphs = segment("news_article", row["full_text"])
        for idx, paragraph in enumerate(paragraphs):
            for match in match_entities(paragraph, entities, fuzzy_threshold=fuzzy_threshold):
                units.append(
                    {
                        "unit_id": f"{row['id']}-{idx}-{match.entity_id}",
                        "article_id": row["id"],
                        "paragraph_index": idx,
                        "paragraph_id": f"{row['id']}-{idx}",
                        "entity_id": match.entity_id,
                        "entity_name": entity_names[match.entity_id],
                        "paragraph_text": paragraph,
                        "source_name": row["source_name"],
                        "canonical_link": row["canonical_link"],
                    }
                )
    return units


def pick_round1_articles(article_ids: list[str], rng: random.Random) -> tuple[list[str], list[str]]:
    """Embaralha TODOS os artigos elegíveis uma vez (ordem estável dado
    SEED) e separa o prefixo desta rodada do restante reservado -- uma
    rodada 2 que aumente ROUND1_ARTICLE_COUNT reaproveita exatamente os
    mesmos artigos já sorteados pra rodada 1, só estende o prefixo."""
    shuffled = list(article_ids)
    rng.shuffle(shuffled)
    return shuffled[:ROUND1_ARTICLE_COUNT], shuffled[ROUND1_ARTICLE_COUNT:]


def split_articles(article_ids: list[str], rng: random.Random) -> dict[str, set[str]]:
    shuffled = list(article_ids)
    rng.shuffle(shuffled)
    n = len(shuffled)
    n_train = round(n * SPLIT_RATIOS["train"])
    n_val = round(n * SPLIT_RATIOS["validation"])
    return {
        "train": set(shuffled[:n_train]),
        "validation": set(shuffled[n_train : n_train + n_val]),
        "test": set(shuffled[n_train + n_val :]),
    }


def assign_reviewers(units: list[dict], split_name: str, rng: random.Random) -> None:
    """Marca cada unidade com split, is_overlap e a lista de revisores
    responsáveis -- overlap vai para todos os REVIEWERS, o resto é
    dividido em round-robin disjunto."""
    pool = list(units)
    rng.shuffle(pool)
    n_overlap = round(len(pool) * OVERLAP_FRACTION)
    overlap_units, solo_units = pool[:n_overlap], pool[n_overlap:]

    for unit in overlap_units:
        unit["split"] = split_name
        unit["is_overlap"] = True
        unit["reviewers"] = list(REVIEWERS)

    for i, unit in enumerate(solo_units):
        unit["split"] = split_name
        unit["is_overlap"] = False
        unit["reviewers"] = [REVIEWERS[i % len(REVIEWERS)]]


def write_manifest(units: list[dict]) -> None:
    path = OUT_DIR / "manifest.csv"
    fieldnames = [
        "unit_id", "article_id", "paragraph_index", "paragraph_id", "entity_id", "entity_name",
        "split", "is_overlap", "reviewers", "source_name", "canonical_link", "paragraph_text",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for u in units:
            writer.writerow({**u, "reviewers": ";".join(u["reviewers"])})
    print(f"escrito {path} ({len(units)} linhas)")


def write_reviewer_sheets(units: list[dict]) -> None:
    by_reviewer: dict[str, list[dict]] = {r: [] for r in REVIEWERS}
    for u in units:
        for r in u["reviewers"]:
            by_reviewer[r].append(u)

    fieldnames = ["paragraph_id", "entity_name", "paragraph_text", "label", "intensity", "notes"]
    for reviewer, rows in by_reviewer.items():
        rows_sorted = sorted(rows, key=lambda u: (u["article_id"], u["paragraph_index"]))
        path = OUT_DIR / f"{reviewer}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for u in rows_sorted:
                writer.writerow(
                    {
                        "paragraph_id": u["paragraph_id"],
                        "entity_name": u["entity_name"],
                        "paragraph_text": u["paragraph_text"],
                        "label": "",
                        "intensity": "",
                        "notes": "",
                    }
                )
        print(f"escrito {path} ({len(rows_sorted)} linhas, {sum(1 for u in rows_sorted if u['is_overlap'])} de sobreposição)")


def main() -> None:
    rng = random.Random(SEED)
    all_units = load_units()

    all_article_ids = sorted({u["article_id"] for u in all_units})
    round1_ids, reserved_ids = pick_round1_articles(all_article_ids, rng)
    round1_id_set = set(round1_ids)
    units = [u for u in all_units if u["article_id"] in round1_id_set]

    print(
        f"rodada 1: {len(round1_ids)} de {len(all_article_ids)} artigos elegíveis "
        f"({len(units)} de {len(all_units)} unidades) -- {len(reserved_ids)} artigos reservados pra rodada 2"
    )

    split_of_article = split_articles(round1_ids, rng)

    for split_name, ids in split_of_article.items():
        split_units = [u for u in units if u["article_id"] in ids]
        assign_reviewers(split_units, split_name, rng)
        print(f"{split_name}: {len(ids)} artigos, {len(split_units)} unidades")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_manifest(units)
    write_reviewer_sheets(units)


if __name__ == "__main__":
    main()
