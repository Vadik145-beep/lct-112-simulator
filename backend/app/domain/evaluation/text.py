"""Text normalization shared by both engines: case, «ё», punctuation, street types, keywords."""

from __future__ import annotations

import re
from collections.abc import Iterable

from rapidfuzz import fuzz

from app.domain.reference_data import CALLER_TOPICS, SERVICE_CALL_FACTS

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")

# Street type abbreviations → full word (memo and tickets use both forms).
STREET_TYPES: dict[str, str] = {
    "ул": "улица",
    "улица": "улица",
    "пр": "проспект",
    "просп": "проспект",
    "пр-т": "проспект",
    "проспект": "проспект",
    "пер": "переулок",
    "переулок": "переулок",
    "ш": "шоссе",
    "шоссе": "шоссе",
    "б-р": "бульвар",
    "бул": "бульвар",
    "бульвар": "бульвар",
    "пл": "площадь",
    "площадь": "площадь",
    "наб": "набережная",
    "набережная": "набережная",
    "пр-д": "проезд",
    "проезд": "проезд",
    "туп": "тупик",
    "тупик": "тупик",
    "ал": "аллея",
    "аллея": "аллея",
    "мкр": "микрорайон",
    "микрорайон": "микрорайон",
    "кв-л": "квартал",
    "квартал": "квартал",
}
_STREET_TYPE_WORDS = set(STREET_TYPES.values())
_STREET_TOKEN = re.compile(r"[а-яa-z0-9]+(?:-[а-яa-z0-9]+)*", re.IGNORECASE)


def normalize_text(text: str | None) -> str:
    """Lower case, ё→е, punctuation removed, spaces collapsed."""
    if not text:
        return ""
    lowered = text.lower().replace("ё", "е")
    return _SPACES.sub(" ", _PUNCT.sub(" ", lowered)).strip()


def normalize_street(text: str | None) -> str:
    """Street name with the type expanded and moved to the end: «ул. Берзарина» and
    «улица Берзарина» both become «берзарина улица»; «Б. Сухаревский пер.» keeps its
    words. The type is kept so «Ленинский проспект» and «Ленинская улица» stay different."""
    if not text:
        return ""
    lowered = text.lower().replace("ё", "е")
    tokens = _STREET_TOKEN.findall(lowered)
    types: list[str] = []
    words: list[str] = []
    for token in tokens:
        full = STREET_TYPES.get(token) or STREET_TYPES.get(token.replace("-", ""))
        if full:
            types.append(full)
        else:
            words.append(token)
    return " ".join([*words, *types])


def street_core(text: str | None) -> str:
    """Street name without its type word: «берзарина»."""
    return " ".join(w for w in normalize_street(text).split() if w not in _STREET_TYPE_WORDS)


def street_ratio(a: str | None, b: str | None) -> float:
    """rapidfuzz ratio (0..100) of two street names after normalization; the type word is
    compared separately so a different type lowers the ratio only when both sides name one."""
    if not a or not b:
        return 0.0
    core = fuzz.ratio(street_core(a), street_core(b))
    types_a = [w for w in normalize_street(a).split() if w in _STREET_TYPE_WORDS]
    types_b = [w for w in normalize_street(b).split() if w in _STREET_TYPE_WORDS]
    if types_a and types_b and types_a != types_b:
        core *= 0.9
    return round(core, 1)


def streets_match(a: str | None, b: str | None, threshold: float = 90.0) -> bool:
    return street_ratio(a, b) >= threshold


def normalize_house(text: str | None) -> str:
    """«21», «д. 21», «21А», «21 а» → «21а»."""
    value = normalize_text(text)
    value = re.sub(r"^(дом|д)\s*", "", value)
    return value.replace(" ", "")


def contains_keyword(text: str, keyword: str, threshold: float = 88.0) -> bool:
    """Whether a keyword (word or phrase) occurs in the text, allowing inflection and typos:
    exact normalized substring first, then a fuzzy partial match of the normalized forms."""
    haystack = normalize_text(text)
    needle = normalize_text(keyword)
    if not haystack or not needle:
        return False
    if needle in haystack:
        return True
    return fuzz.partial_ratio(needle, haystack) >= threshold


def keyword_coverage(text: str, keywords: Iterable[str]) -> tuple[list[str], list[str]]:
    found, missing = [], []
    for keyword in keywords:
        (found if contains_keyword(text, keyword) else missing).append(keyword)
    return found, missing


# (topic code, [(normalized keyword, whole word)]): a keyword written with a trailing space in
# ``caller_topics`` («дом ») matches the whole word only, others match at a word start (stems).
_TOPIC_KEYWORDS: list[tuple[str, list[tuple[str, bool]]]] = [
    (t["code"], [(normalize_text(k), k.endswith(" ")) for k in t["keywords"]])
    for t in CALLER_TOPICS
    if t["keywords"]
]
# The same table for the facts a dispatcher passes to a service officer (issue #36).
SERVICE_FACT_KEYWORDS: list[tuple[str, list[tuple[str, bool]]]] = [
    (f["code"], [(normalize_text(k), k.endswith(" ")) for k in f["keywords"]])
    for f in SERVICE_CALL_FACTS
]


def detect_service_facts(text: str) -> list[str]:
    """Facts of a service call the dispatcher's phrase mentions (by keywords)."""
    return detect_topics(text, SERVICE_FACT_KEYWORDS)


def detect_topics(
    text: str, table: list[tuple[str, list[tuple[str, bool]]]] | None = None
) -> list[str]:
    """Topics a phrase touches, by the keyword lists of ``caller_topics`` (or of another
    table, e.g. the facts of a service call). Used when the dialog engine did not label a
    turn. Order follows the table's ``order``."""
    normalized = normalize_text(text)
    if not normalized:
        return []
    padded = f" {normalized} "
    found = []
    for code, keywords in table if table is not None else _TOPIC_KEYWORDS:
        for keyword, whole_word in keywords:
            # Keywords are stems («пострадавш») or phrases; match at a word start.
            needle = f" {keyword} " if whole_word else f" {keyword}"
            if needle in padded:
                found.append(code)
                break
    return found
