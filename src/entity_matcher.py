from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from rapidfuzz import fuzz, process

from entities_store import Entity


@dataclass(frozen=True)
class EntityMatch:
    entity_id: str
    entity_type: str  # 'politico' | 'partido' (vem de Entity.entity_type)
    match_type: str  # 'exact' | 'fuzzy'
    score: float  # 0.0-1.0
    matched_alias: str


_WORD_RE = re.compile(r"\w+", re.UNICODE)

# Siglas de partido que são, ao mesmo tempo, palavra comum do português --
# match case-insensitive causaria falso positivo (ex.: "os republicanos
# americanos", cobertura de política internacional que já aparece no corpus
# real, casaria com o partido brasileiro Republicanos). Validado contra
# data/raw/articles_202608191040.csv (229 ocorrências reais de
# "Republicanos"): quando é o partido de verdade, o texto SEMPRE escreve em
# Title Case ("Republicanos", "Avante"), nunca em CAIXA ALTA nem em
# minúsculo isolado no meio da frase. AGIR e DEMOCRATA são mais ambíguos
# ("agir"/"democrata" também aparecem como palavra comum minúscula no meio
# de frase), mas exigir Title Case ainda reduz bastante o risco -- ver
# data/annotation/pendencias-tecnicas.md, item 3.
#
# A chave é sempre a forma normalizada (UPPER) do alias, porque a fonte de
# dados (TSE, via `entidade_matching`) guarda sigla de partido em CAIXA
# ALTA (ex.: "REPUBLICANOS") -- não dá pra confiar na capitalização de
# entrada pra decidir SE o alias precisa de tratamento especial. O valor é
# a forma Title Case correta, usada como o padrão comparado contra o texto,
# já que é assim que a sigla aparece no texto real.
_CASE_SENSITIVE_ALIASES: dict[str, str] = {
    "REPUBLICANOS": "Republicanos",
    "AVANTE": "Avante",
    "CIDADANIA": "Cidadania",
    "PODEMOS": "Podemos",
    "SOLIDARIEDADE": "Solidariedade",
    "AGIR": "Agir",
    "DEMOCRATA": "Democrata",
}


# Cada alias vira um regex distinto. O cache interno do módulo `re` guarda só
# 512 padrões, e o volume real da migração pra `entidade_matching` são ~4.100
# aliases (~1.400 entidades) -- ou seja, o cache vira thrash e TODOS os
# padrões são recompilados a cada parágrafo. Medido em
# scripts/benchmark_entity_matcher.py: 30,9 ms/parágrafo só de exact match,
# contra 5,7 ms com os padrões já compilados. Cache próprio, dimensionado pro
# volume esperado -- se estourar, o pior caso é voltar a recompilar (degrada,
# não quebra).
@lru_cache(maxsize=8192)
def _alias_pattern(alias: str, flags: int) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(alias) + r"\b", flags)


def _fold_diacritics(text: str) -> str:
    """Remove diacríticos (acento agudo, til, cedilha, circunflexo etc.),
    mantendo a letra base -- "á"/"ã"/"a" e "ç"/"c" tornam-se equivalentes
    pra fins de comparação. NUNCA usado pra alterar texto salvo/exibido, só
    como forma auxiliar de comparação.

    Motivo (caso real, reportado pela Bea em 2026-08-30 e confirmado
    rodando o parágrafo de verdade): a acentuação em `entidade_matching`
    (TSE) é inconsistente -- ex.: aliases de "Flávio Bolsonaro" cadastrados
    como `["FLAVIO NANTES BOLSONARO", "FLAVIO BOLSONARO"]`, sem acento,
    mas o texto jornalístico real escreve "Flávio Bolsonaro" (com acento).
    Sem dobrar o acento, o match exato falha (caracteres diferentes) E o
    fuzzy também falha: `fuzz.ratio("flavio", "flávio")` é 83,3%, abaixo do
    `fuzzy_threshold` default de 85% -- a diferença de UM caractere
    acentuado já é suficiente pra derrubar o score abaixo do corte."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


@lru_cache(maxsize=8192)
def _fold_diacritics_alias(text: str) -> str:
    """Wrapper com cache de `_fold_diacritics`, só pra strings do tamanho de
    alias/palavra -- aliases se repetem em toda chamada de `match_entities`
    (~4.100 no volume real), então o custo de normalizar sai da conta
    depois da primeira vez. NÃO usar pra parágrafo (string longa, quase
    nunca repete de uma chamada pra outra -- encheria o cache à toa, sem
    ganho de acerto)."""
    return _fold_diacritics(text)


def _exact_match(alias: str, folded_paragraph: str) -> bool:
    """Match exato por palavra (`\\b...\\b`), case-insensitive e
    acento-insensitive por padrão (`folded_paragraph` já veio sem
    diacríticos de `match_entities` -- ver `_fold_diacritics`).

    Exceção: aliases em `_CASE_SENSITIVE_ALIASES` (siglas de partido que
    coincidem com palavra comum do português) exigem Title Case exato
    (case-sensitive) -- ver comentário acima do dicionário. Nenhum desses 7
    aliases tem acento, então dobrar diacrítico no parágrafo não muda nada
    pra esse caminho. Pra todo o resto (nomes de político, outros
    partidos), o comportamento continua IGNORECASE normal, agora também
    ignorando acentuação."""
    special_form = _CASE_SENSITIVE_ALIASES.get(alias.upper())
    if special_form is not None:
        return _alias_pattern(special_form, re.UNICODE).search(folded_paragraph) is not None

    folded_alias = _fold_diacritics_alias(alias)
    return _alias_pattern(folded_alias, re.IGNORECASE | re.UNICODE).search(folded_paragraph) is not None


def _fuzzy_match_score(alias: str, folded_paragraph: str) -> float:
    """Compara o alias contra janelas de palavras do parágrafo (não a string
    inteira) -- `fuzz.partial_ratio` sobre o parágrafo inteiro casaria "Lula"
    como substring de "Lulalândia" com score 1.0, já que ele ignora fronteira
    de palavra. Aqui cada janela tem o mesmo número de palavras que o alias.

    Dentro de cada janela, o score é o MÍNIMO do `fuzz.ratio` palavra-a-palavra
    (mesma posição no alias e na janela), não o `fuzz.ratio` da janela inteira
    concatenada. Comparar a janela inteira deixa uma palavra-âncora longa do
    alias dominar o score de caracteres -- por exemplo, `fuzz.ratio("presidente
    lula", "presidente da")` dá 0.857, porque "presidente" (10 chars) pesa
    muito mais que "lula"/"da" (4 chars), então "presidente da Câmara" casaria
    falsamente com o alias "presidente Lula" mesmo sem citar Lula. Comparando
    palavra por palavra e usando o mínimo, essa mesma frase cai pro ratio
    baixo de "lula" vs "da" e é rejeitada, enquanto um erro de digitação real
    (ex.: "presidente Lulla") mantém as duas palavras com ratio alto e
    continua sendo aceito. Para alias de uma palavra só, isso reduz ao mesmo
    comportamento de antes.

    `folded_paragraph` já vem sem diacríticos de `match_entities` (ver
    `_fold_diacritics`) -- os aliases são dobrados aqui, palavra por
    palavra, com a versão em cache (`_fold_diacritics_alias`). Sem isso,
    uma única letra acentuada derruba o ratio o suficiente pra ficar abaixo
    do threshold (ex.: "flavio" vs "flávio" = 83,3%, abaixo do default de
    85%) -- caso real, ver `_fold_diacritics`."""
    alias_words = alias.split()
    window_size = len(alias_words)
    paragraph_words = _WORD_RE.findall(folded_paragraph)
    if window_size == 0 or len(paragraph_words) < window_size:
        return 0.0

    alias_words_folded = [_fold_diacritics_alias(w.lower()) for w in alias_words]
    best_score = 0.0
    for i in range(len(paragraph_words) - window_size + 1):
        window_words = paragraph_words[i : i + window_size]
        score = min(
            fuzz.ratio(alias_word, window_word.lower())
            for alias_word, window_word in zip(alias_words_folded, window_words)
        ) / 100.0
        if score > best_score:
            best_score = score

    return best_score


# ---------------------------------------------------------------------------
# Pré-filtro de candidatos (otimização de performance, não muda o resultado)
# ---------------------------------------------------------------------------
#
# Sem pré-filtro, cada parágrafo paga `_exact_match` + `_fuzzy_match_score`
# para TODOS os aliases de TODAS as entidades. Com o volume real (~1.400
# entidades / ~4.100 aliases) isso são ~4.100 regex + ~4.100 varreduras de
# janela por parágrafo -- e, medido, quase nada disso tem chance de casar:
# sobram ~12 aliases plausíveis por parágrafo.
#
# O filtro se apoia numa propriedade dos dois caminhos de match:
#
#   * `_fuzzy_match_score` só devolve >= threshold se existir uma janela em
#     que TODA palavra do alias tem `fuzz.ratio` >= threshold com a palavra
#     da mesma posição. Logo, é condição NECESSÁRIA que cada palavra do
#     alias tenha ratio >= threshold com ALGUMA palavra do parágrafo.
#
#   * `_exact_match` (`\balias\b`) só casa se o trecho do parágrafo for o
#     alias inteiro, com fronteira de palavra nas pontas -- então toda
#     palavra `\w+` do alias aparece como palavra `\w+` completa do
#     parágrafo, o que é o caso particular ratio == 100.
#
# Então basta descobrir, uma vez por parágrafo, quais palavras do vocabulário
# de aliases têm alguma palavra parecida no parágrafo. Isso é uma comparação
# de N palavras do parágrafo contra M palavras de vocabulário, feita dentro
# do rapidfuzz (`process.extract`, laço em C com corte por score) em vez de
# ~4.100 chamadas Python.
#
# O filtro é conservador de propósito: ele só descarta alias que
# comprovadamente não pode casar. Alias que sobrevive continua passando pelo
# `_exact_match`/`_fuzzy_match_score` originais, sem atalho -- o resultado de
# `match_entities` é idêntico ao de antes. Isso vale também pros aliases em
# `_CASE_SENSITIVE_ALIASES`: o filtro compara em minúsculo (ignorando
# maiúscula/minúscula), então nunca descarta um alias que só falharia depois
# por causa da exigência de Title Case -- só descarta quando a palavra nem
# parecida está no parágrafo.
#
# Premissa registrada: a presença de uma palavra é comparada em `str.lower()`
# sobre texto já sem diacríticos (`_fold_diacritics`), enquanto `_exact_match`
# usa `re.IGNORECASE` também sobre texto já sem diacríticos. Os dois divergem
# só em caracteres Unicode exóticos (ex.: 'ſ' casa com 's' no IGNORECASE mas
# não no lower()), que não ocorrem em texto jornalístico PT-BR nem em nomes
# vindos do TSE. `_fuzzy_match_score` já assumia o mesmo (compara em
# `.lower()` sobre texto dobrado).


@dataclass(frozen=True)
class _PreparedAlias:
    alias: str
    # Índices, no vocabulário, das palavras que `_fuzzy_match_score` compara
    # (ou seja, `alias.split()` em minúsculo).
    fuzzy_word_ids: tuple[int, ...]
    # Idem para as palavras `\w+` do alias, que é o que `_exact_match` exige.
    # Fica None quando as duas tokenizações coincidem (caso da esmagadora
    # maioria dos aliases, que só têm letras e espaços) -- aí um teste só
    # basta. Difere em aliases com pontuação interna, ex.: "Jean-Pierre",
    # onde `split()` dá ["jean-pierre"] e `\w+` dá ["jean", "pierre"].
    exact_word_ids: tuple[int, ...] | None


@dataclass(frozen=True)
class _AliasIndex:
    vocabulary: tuple[str, ...]
    per_entity: tuple[tuple[Entity, tuple[_PreparedAlias, ...]], ...]


def _build_alias_index(entities: list[Entity]) -> _AliasIndex:
    vocabulary: list[str] = []
    vocabulary_ids: dict[str, int] = {}

    def word_id(word: str) -> int:
        existing = vocabulary_ids.get(word)
        if existing is not None:
            return existing
        new_id = len(vocabulary)
        vocabulary_ids[word] = new_id
        vocabulary.append(word)
        return new_id

    per_entity: list[tuple[Entity, tuple[_PreparedAlias, ...]]] = []
    for entity in entities:
        prepared: list[_PreparedAlias] = []
        for alias in entity.all_match_candidates():
            fuzzy_words = tuple(_fold_diacritics_alias(word.lower()) for word in alias.split())
            exact_words = tuple(_fold_diacritics_alias(word.lower()) for word in _WORD_RE.findall(alias))
            prepared.append(
                _PreparedAlias(
                    alias=alias,
                    fuzzy_word_ids=tuple(word_id(word) for word in fuzzy_words),
                    exact_word_ids=(
                        None if exact_words == fuzzy_words else tuple(word_id(word) for word in exact_words)
                    ),
                )
            )
        per_entity.append((entity, tuple(prepared)))

    return _AliasIndex(vocabulary=tuple(vocabulary), per_entity=tuple(per_entity))


# Cache de uma entrada só: em produção (`src/pipeline.py`) a mesma lista de
# entidades é reusada para todos os parágrafos de todas as mensagens, então
# uma entrada cobre 100% dos casos. A validação compara a lista atual contra
# o snapshot guardado -- tupla de objetos idênticos, então a comparação cai no
# atalho de identidade do CPython e custa um punhado de microssegundos, mas
# protege contra a lista ter sido alterada entre chamadas.
_cached_entities: tuple[Entity, ...] | None = None
_cached_index: _AliasIndex | None = None


def _alias_index_for(entities: list[Entity]) -> _AliasIndex:
    global _cached_entities, _cached_index

    snapshot = tuple(entities)
    if _cached_index is not None and _cached_entities == snapshot:
        return _cached_index

    index = _build_alias_index(entities)
    _cached_entities = snapshot
    _cached_index = index
    return index


def reset_alias_index_cache() -> None:
    """Descarta o índice de aliases em memória. A correção NÃO depende disso
    (`_alias_index_for` revalida o cache a cada chamada) -- serve para liberar
    memória depois de trocar a lista de entidades num processo longo, e para
    isolar testes que medem o comportamento do próprio cache."""
    global _cached_entities, _cached_index
    _cached_entities = None
    _cached_index = None


def _candidate_word_hits(index: _AliasIndex, paragraph_words_lower: list[str], fuzzy_threshold: float) -> list[bool]:
    """Para cada palavra do vocabulário de aliases, diz se alguma palavra do
    parágrafo chega ao threshold contra ela. Uma passada por parágrafo,
    reaproveitada por todos os aliases."""
    if fuzzy_threshold <= 0.0:
        # Threshold nulo aceita qualquer coisa: não há o que descartar.
        return [True] * len(index.vocabulary)

    # Clamp em 100: com threshold > 1.0 nenhum match fuzzy é possível (o score
    # é 0-1), mas o exact match ainda precisa das palavras presentes na
    # íntegra -- ratio 100. A margem de 1e-9 evita que ruído de ponto
    # flutuante (`ratio / 100 >= t` aqui vs. `ratio >= t * 100` no rapidfuzz)
    # descarte um candidato que ficaria exatamente em cima do threshold.
    score_cutoff = min(fuzzy_threshold, 1.0) * 100.0 - 1e-9

    hits = [False] * len(index.vocabulary)
    for token in dict.fromkeys(paragraph_words_lower):
        for _matched, _score, vocabulary_id in process.extract(
            token, index.vocabulary, scorer=fuzz.ratio, score_cutoff=score_cutoff, limit=None
        ):
            hits[vocabulary_id] = True
    return hits


def _may_match(prepared: _PreparedAlias, hits: list[bool]) -> bool:
    """False só quando o alias comprovadamente não pode casar de jeito nenhum
    neste parágrafo. Em caso de dúvida devolve True e deixa
    `_exact_match`/`_fuzzy_match_score` decidirem -- errar para o lado
    permissivo custa tempo, errar para o lado restritivo perde match."""
    for word_id in prepared.fuzzy_word_ids:
        if not hits[word_id]:
            break
    else:
        return True

    exact_word_ids = prepared.exact_word_ids
    if exact_word_ids is None:
        return False
    for word_id in exact_word_ids:
        if not hits[word_id]:
            return False
    return True


def match_entities(paragraph: str, entities: list[Entity], *, fuzzy_threshold: float) -> list[EntityMatch]:
    """Identifica quais entidades são mencionadas no parágrafo, via match
    exato (por palavra, case-insensitive) OU fuzzy (rapidfuzz, comparando
    janelas de palavras do parágrafo com o mesmo tamanho do alias -- não a
    string inteira), o que vier primeiro por entidade -- não é NER, é uma
    decisão consciente registrada em classifier-prisma/context.md, seção 9.

    `fuzzy_threshold` é 0-1 (ex.: 0.85) -- comparável a `entity_match_score`
    persistido em `classifications`. Um match fuzzy abaixo do threshold é
    descartado (não gera EntityMatch) -- não existe "match fraco demais mas
    registrado mesmo assim" nesta v1.

    Cada entidade contribui no máximo um EntityMatch por parágrafo, com o
    melhor apelido encontrado (maior score) -- múltiplas menções da mesma
    entidade no mesmo parágrafo não geram matches duplicados.

    Aliases em `_CASE_SENSITIVE_ALIASES` (ver `_exact_match`) NUNCA caem no
    fallback fuzzy: `_fuzzy_match_score` compara em minúsculo dos dois
    lados, então "republicanos" ou "REPUBLICANOS" bateriam com score 1.0
    ali mesmo com o exact match bloqueado -- reabriria exatamente o mesmo
    falso positivo que o Title Case exigido no exact match existe pra
    evitar.

    Aliases sem nenhuma chance de casar são descartados antes de pagar o
    custo de regex/fuzzy -- ver o bloco "Pré-filtro de candidatos" acima.
    É só performance: o conjunto de matches devolvido é o mesmo de antes.

    Acentuação é ignorada na comparação (`_fold_diacritics`) -- o parágrafo
    é dobrado uma vez só aqui (string longa, não cacheada; ver
    `_fold_diacritics_alias` pro lado do alias, que É cacheado por se
    repetir em toda chamada). Caso real que motivou isso: alias cadastrado
    sem acento ("FLAVIO BOLSONARO") não batia com o texto real, que usa
    acento ("Flávio Bolsonaro") -- nem no exato, nem no fuzzy (a diferença
    de uma letra acentuada já derruba o ratio abaixo do threshold)."""
    index = _alias_index_for(entities)
    folded_paragraph = _fold_diacritics(paragraph)
    paragraph_words_lower = [word.lower() for word in _WORD_RE.findall(folded_paragraph)]
    hits = _candidate_word_hits(index, paragraph_words_lower, fuzzy_threshold)

    matches: list[EntityMatch] = []

    for entity, prepared_aliases in index.per_entity:
        best: EntityMatch | None = None

        for prepared in prepared_aliases:
            if not _may_match(prepared, hits):
                continue

            alias = prepared.alias
            if _exact_match(alias, folded_paragraph):
                best = EntityMatch(
                    entity_id=entity.id,
                    entity_type=entity.entity_type,
                    match_type="exact",
                    score=1.0,
                    matched_alias=alias,
                )
                break  # exato já é o melhor resultado possível -- não precisa continuar

            if alias.upper() in _CASE_SENSITIVE_ALIASES:
                continue  # sem fallback fuzzy pra esses -- ver docstring acima

            fuzzy_score = _fuzzy_match_score(alias, folded_paragraph)
            if fuzzy_score >= fuzzy_threshold and (best is None or fuzzy_score > best.score):
                best = EntityMatch(
                    entity_id=entity.id,
                    entity_type=entity.entity_type,
                    match_type="fuzzy",
                    score=fuzzy_score,
                    matched_alias=alias,
                )

        if best is not None:
            matches.append(best)

    return matches
