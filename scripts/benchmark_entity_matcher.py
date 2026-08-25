"""Mede o custo real de `entity_matcher.match_entities` (src/entity_matcher.py)
contra o volume que a migração pra `entidade_matching` vai trazer: ~1.393
entidades reais (1.363 políticos + 30 partidos), casadas por PARÁGRAFO (não
por artigo -- `run_pipeline` em src/pipeline.py chama `match_entities` uma
vez por parágrafo segmentado, então o custo multiplica pelo número de
parágrafos de cada notícia).

Este script NÃO se conecta a banco nenhum e NÃO lê a tabela real
`entidade_matching` (ainda sem acesso programático). As ~1.400 entidades são
sintéticas, geradas em memória com seed fixa (reprodutível) -- só precisamos
do volume/formato (`Entity` com canonical_name de 2-4 palavras, 1-2 aliases,
mistura de entity_type político/partido), não dos nomes reais do TSE.

Os parágrafos usados no benchmark SÃO reais: vêm de
data/raw/articles_202608191040.csv, segmentados via src/segmentation.py
(mesma função usada em produção), pra ter tamanho/estrutura de texto
realista.

Uso:
    .venv/bin/python scripts/benchmark_entity_matcher.py

Não modifica nenhum arquivo de produção -- é só medição. Ver conclusão e
recomendação no final da saída.
"""
from __future__ import annotations

import csv
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import segmentation  # noqa: E402  (precisa do sys.path acima)
from entities_store import Entity, load_and_validate  # noqa: E402
from entity_matcher import match_entities  # noqa: E402

ARTICLES_CSV = BASE_DIR / "data" / "raw" / "articles_202608191040.csv"
REAL_ENTITIES_YAML = BASE_DIR / "config" / "entities.yaml"

FUZZY_THRESHOLD = 0.85  # mesmo valor de config/settings.yaml -- ver docstring do módulo

# Volume alvo da migração: 1.363 políticos + 30 partidos = 1.393 entidades.
N_POLITICIANS = 1_363
N_PARTIES = 30

N_REAL_PARAGRAPHS = 25  # quantidade de parágrafos reais usados no benchmark
N_REPEATS_PER_PARAGRAPH = 5  # repetições por parágrafo, pra reduzir ruído de medição

ESTIMATED_MESSAGES_PER_HOUR = 1_000  # cenário de throughput citado na tarefa


# ---------------------------------------------------------------------------
# Geração de entidades sintéticas (determinística, sem I/O)
# ---------------------------------------------------------------------------

_FIRST_NAMES = [
    "Carlos", "Marcos", "João", "Paulo", "Antônio", "Francisco", "José",
    "Pedro", "Luís", "Rafael", "Eduardo", "Ricardo", "Fernando", "Roberto",
    "Sérgio", "André", "Bruno", "Gustavo", "Rodrigo", "Felipe", "Marina",
    "Ana", "Maria", "Juliana", "Fernanda", "Camila", "Patrícia", "Renata",
    "Adriana", "Cristina", "Vera", "Sandra", "Beatriz", "Larissa", "Vanessa",
    "Débora", "Cláudia", "Simone", "Rosana", "Tatiana",
]

_SURNAMES = [
    "Silva", "Souza", "Oliveira", "Santos", "Pereira", "Costa", "Rodrigues",
    "Almeida", "Nascimento", "Lima", "Araújo", "Fernandes", "Carvalho",
    "Gomes", "Martins", "Rocha", "Ribeiro", "Alves", "Monteiro", "Barbosa",
    "Cardoso", "Correia", "Teixeira", "Moreira", "Cavalcanti", "Dias",
    "Castro", "Campos", "Freitas", "Barros", "Pinto", "Machado", "Ramos",
    "Vieira", "Nunes", "Mendes", "Farias", "Duarte", "Guimarães", "Peixoto",
]

_CONNECTORS = ["da", "de", "dos", "das"]

_TITLES = ["Deputado", "Deputada", "Senador", "Senadora", "Vereador", "Vereadora", "Prefeito", "Prefeita"]

_PARTY_ADJECTIVES = [
    "Nacional", "Popular", "Democrático", "Liberal", "Social", "Progressista",
    "Trabalhista", "Republicano", "Renovador", "Cidadão",
]
_PARTY_NOUNS = [
    "Brasileiro", "do Povo", "da União", "da Renovação", "do Trabalho",
    "da Democracia", "da República", "do Progresso", "Nacional", "Cidadão",
]


def _generate_politician_name(rng, first_names: list[str], surnames: list[str]) -> str:
    style = rng.choice(("2word", "3word_connector", "4word"))
    first = rng.choice(first_names)
    if style == "2word":
        return f"{first} {rng.choice(surnames)}"
    if style == "3word_connector":
        return f"{first} {rng.choice(_CONNECTORS)} {rng.choice(surnames)}"
    return f"{first} {rng.choice(surnames)} {rng.choice(surnames)}"


def _generate_party_name(rng) -> str:
    return f"Partido {rng.choice(_PARTY_ADJECTIVES)} {rng.choice(_PARTY_NOUNS)}"


def _acronym(name: str) -> str:
    """Gera uma sigla plausível a partir das iniciais do nome (ex.: 'Partido
    Nacional Popular' -> 'PNP'), como alias de partido -- mesmo padrão de
    config/entities.yaml (canonical_name + sigla como aliases)."""
    return "".join(word[0].upper() for word in name.split() if word.lower() not in _CONNECTORS)


def generate_synthetic_entities(n_politicians: int, n_parties: int, *, seed: int = 42) -> list[Entity]:
    """Gera entidades sintéticas em memória, sem ler nenhum arquivo real nem
    conectar a banco. Determinístico (seed fixa) -- roda igual toda vez."""
    import random

    rng = random.Random(seed)
    entities: list[Entity] = []
    seen_names: set[str] = set()

    politicians_generated = 0
    while politicians_generated < n_politicians:
        name = _generate_politician_name(rng, _FIRST_NAMES, _SURNAMES)
        if name in seen_names:
            continue
        seen_names.add(name)

        surname = name.split()[-1]
        title = rng.choice(_TITLES)
        aliases = (surname, f"{title} {surname}")

        entities.append(
            Entity(
                id=f"synthetic-politician-{politicians_generated}",
                canonical_name=name,
                entity_type="politician",
                aliases=aliases,
            )
        )
        politicians_generated += 1

    parties_generated = 0
    while parties_generated < n_parties:
        name = _generate_party_name(rng)
        if name in seen_names:
            continue
        seen_names.add(name)

        entities.append(
            Entity(
                id=f"synthetic-party-{parties_generated}",
                canonical_name=name,
                entity_type="party",
                aliases=(_acronym(name),),
            )
        )
        parties_generated += 1

    return entities


# ---------------------------------------------------------------------------
# Carga de parágrafos reais (dado real, formato/volume realista)
# ---------------------------------------------------------------------------


def load_real_paragraphs(csv_path: Path, n_paragraphs: int) -> list[str]:
    """Lê data/raw/articles_202608191040.csv, segmenta full_text via
    src/segmentation.py (mesma função de produção) e devolve N parágrafos
    reais escolhidos por tamanho (do mais curto ao mais longo, espaçados de
    forma uniforme) -- pra ter variedade de tamanho de texto no benchmark,
    de forma determinística (sem random.sample)."""
    all_paragraphs: list[str] = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("extraction_status") != "success" or not row.get("full_text"):
                continue
            all_paragraphs.extend(segmentation.segment("news_article", row["full_text"]))
            if len(all_paragraphs) >= n_paragraphs * 20:
                # já temos parágrafos de sobra pra escolher com variedade
                break

    if len(all_paragraphs) < n_paragraphs:
        raise RuntimeError(
            f"só {len(all_paragraphs)} parágrafos reais disponíveis em {csv_path}, "
            f"precisa de {n_paragraphs}"
        )

    all_paragraphs.sort(key=len)
    step = len(all_paragraphs) / n_paragraphs
    indices = [int(i * step) for i in range(n_paragraphs)]
    return [all_paragraphs[i] for i in indices]


# ---------------------------------------------------------------------------
# Medição
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioResult:
    label: str
    n_entities: int
    per_paragraph_seconds: list[float]  # já é a média das repetições, uma por parágrafo

    @property
    def mean(self) -> float:
        return statistics.mean(self.per_paragraph_seconds)

    @property
    def median(self) -> float:
        return statistics.median(self.per_paragraph_seconds)

    @property
    def p95(self) -> float:
        data = sorted(self.per_paragraph_seconds)
        idx = min(len(data) - 1, int(0.95 * len(data)))
        return data[idx]

    @property
    def total(self) -> float:
        return sum(self.per_paragraph_seconds)


def benchmark_scenario(label: str, paragraphs: list[str], entities: list[Entity]) -> ScenarioResult:
    # warm-up (não cronometrado) -- evita medir custo de import/JIT-like
    # warm caches do rapidfuzz na primeira chamada.
    match_entities(paragraphs[0], entities, fuzzy_threshold=FUZZY_THRESHOLD)

    per_paragraph_seconds: list[float] = []
    for paragraph in paragraphs:
        samples = []
        for _ in range(N_REPEATS_PER_PARAGRAPH):
            start = time.perf_counter()
            match_entities(paragraph, entities, fuzzy_threshold=FUZZY_THRESHOLD)
            samples.append(time.perf_counter() - start)
        per_paragraph_seconds.append(statistics.mean(samples))

    return ScenarioResult(label=label, n_entities=len(entities), per_paragraph_seconds=per_paragraph_seconds)


def format_ms(seconds: float) -> str:
    return f"{seconds * 1000:.3f} ms"


def main() -> None:
    print("=" * 78)
    print("Benchmark: entity_matcher.match_entities -- 10 entidades (atual) vs.")
    print("~1.393 entidades sintéticas (volume da migração pra entidade_matching)")
    print("=" * 78)

    print(f"\nCarregando entidades reais de {REAL_ENTITIES_YAML} ...")
    real_entities = load_and_validate(REAL_ENTITIES_YAML)
    print(f"  -> {len(real_entities)} entidades reais (baseline atual)")

    print(f"\nGerando entidades sintéticas (seed fixa, {N_POLITICIANS} políticos + {N_PARTIES} partidos) ...")
    synthetic_entities = generate_synthetic_entities(N_POLITICIANS, N_PARTIES)
    print(f"  -> {len(synthetic_entities)} entidades sintéticas")

    print(f"\nCarregando {N_REAL_PARAGRAPHS} parágrafos reais de {ARTICLES_CSV.name} ...")
    paragraphs = load_real_paragraphs(ARTICLES_CSV, N_REAL_PARAGRAPHS)
    lengths = [len(p) for p in paragraphs]
    print(f"  -> {len(paragraphs)} parágrafos (tamanho em chars: min={min(lengths)}, "
          f"mediana={int(statistics.median(lengths))}, max={max(lengths)})")

    print(f"\nMedindo ({N_REPEATS_PER_PARAGRAPH} repetições por parágrafo, threshold={FUZZY_THRESHOLD}) ...")
    baseline = benchmark_scenario("10 entidades (config/entities.yaml atual)", paragraphs, real_entities)
    synthetic = benchmark_scenario(f"{len(synthetic_entities)} entidades sintéticas (volume-alvo)", paragraphs, synthetic_entities)

    print("\n" + "-" * 78)
    print("RESULTADOS POR PARÁGRAFO")
    print("-" * 78)
    for result in (baseline, synthetic):
        print(f"\n{result.label} ({result.n_entities} entidades):")
        print(f"  média:   {format_ms(result.mean)}")
        print(f"  mediana: {format_ms(result.median)}")
        print(f"  p95:     {format_ms(result.p95)}")
        print(f"  min:     {format_ms(min(result.per_paragraph_seconds))}")
        print(f"  max:     {format_ms(max(result.per_paragraph_seconds))}")

    slowdown = synthetic.mean / baseline.mean if baseline.mean > 0 else float("inf")
    print(f"\nFator de desaceleração (média, {synthetic.n_entities} vs. {baseline.n_entities} entidades): {slowdown:.1f}x")

    # --- Estimativa por artigo típico -------------------------------------
    AVG_PARAGRAPHS_PER_ARTICLE = 17  # medido em data/raw/articles_202608191040.csv (amostra de 200 artigos, média ~17.4, mediana 14)

    print("\n" + "-" * 78)
    print(f"ESTIMATIVA PARA UM ARTIGO TÍPICO (~{AVG_PARAGRAPHS_PER_ARTICLE} parágrafos)")
    print("-" * 78)
    baseline_article = baseline.mean * AVG_PARAGRAPHS_PER_ARTICLE
    synthetic_article = synthetic.mean * AVG_PARAGRAPHS_PER_ARTICLE
    print(f"  com 10 entidades:    {format_ms(baseline_article)} de matching por artigo")
    print(f"  com {synthetic.n_entities} entidades: {format_ms(synthetic_article)} de matching por artigo")

    # --- Estimativa de throughput -------------------------------------
    print("\n" + "-" * 78)
    print(f"ESTIMATIVA DE THROUGHPUT ({ESTIMATED_MESSAGES_PER_HOUR} mensagens/hora, 1 artigo = 1 mensagem)")
    print("-" * 78)
    seconds_per_hour_baseline = baseline_article * ESTIMATED_MESSAGES_PER_HOUR
    seconds_per_hour_synthetic = synthetic_article * ESTIMATED_MESSAGES_PER_HOUR
    print(f"  com 10 entidades:    {seconds_per_hour_baseline:.2f} s de matching/hora "
          f"({seconds_per_hour_baseline / 3600 * 100:.3f}% de uma hora, consumidor serial)")
    print(f"  com {synthetic.n_entities} entidades: {seconds_per_hour_synthetic:.2f} s de matching/hora "
          f"({seconds_per_hour_synthetic / 3600 * 100:.3f}% de uma hora, consumidor serial)")
    print("\n  (essa estimativa cobre só o tempo de match_entities -- não inclui")
    print("   segmentação, classify_paragraph, I/O de banco/fila, que também")
    print("   consomem tempo do consumidor por mensagem.)")

    print("\n" + "=" * 78)
    print("Ver recomendação/conclusão no relatório do agente que rodou este script.")
    print("=" * 78)


if __name__ == "__main__":
    main()
