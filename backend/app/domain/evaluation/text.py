"""Text normalization shared by both engines: case, «ё», punctuation, street types, keywords."""

from __future__ import annotations

import re
from collections.abc import Iterable

from rapidfuzz import fuzz

from app.domain.reference_data import CALLER_TOPICS, SERVICE_CALL_FACTS

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_DIGIT = re.compile(r"\d")

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


# Numerals spoken aloud. Speech recognition writes a number either way — the Vapi runs have
# both «дом 81» and «квартира пятая» — so a house or a squad number named in words has to
# read as the same number as one named in digits.
_UNITS: dict[str, int] = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "первый": 1,
    "первая": 1,
    "первое": 1,
    "два": 2,
    "две": 2,
    "второй": 2,
    "вторая": 2,
    "второе": 2,
    "три": 3,
    "третий": 3,
    "третья": 3,
    "третье": 3,
    "четыре": 4,
    "четвертый": 4,
    "четвертая": 4,
    "четвертое": 4,
    "пять": 5,
    "пятый": 5,
    "пятая": 5,
    "пятое": 5,
    "шесть": 6,
    "шестой": 6,
    "шестая": 6,
    "шестое": 6,
    "семь": 7,
    "седьмой": 7,
    "седьмая": 7,
    "седьмое": 7,
    "восемь": 8,
    "восьмой": 8,
    "восьмая": 8,
    "восьмое": 8,
    "девять": 9,
    "девятый": 9,
    "девятая": 9,
    "девятое": 9,
    "десять": 10,
    "десятый": 10,
    "десятая": 10,
    "десятое": 10,
    "одиннадцать": 11,
    "одиннадцатый": 11,
    "двенадцать": 12,
    "двенадцатый": 12,
    "тринадцать": 13,
    "тринадцатый": 13,
    "четырнадцать": 14,
    "четырнадцатый": 14,
    "пятнадцать": 15,
    "пятнадцатый": 15,
    "шестнадцать": 16,
    "шестнадцатый": 16,
    "семнадцать": 17,
    "семнадцатый": 17,
    "восемнадцать": 18,
    "восемнадцатый": 18,
    "девятнадцать": 19,
    "девятнадцатый": 19,
}
_TENS: dict[str, int] = {
    "двадцать": 20,
    "двадцатый": 20,
    "тридцать": 30,
    "тридцатый": 30,
    "сорок": 40,
    "сороковой": 40,
    "пятьдесят": 50,
    "пятидесятый": 50,
    "шестьдесят": 60,
    "шестидесятый": 60,
    "семьдесят": 70,
    "семидесятый": 70,
    "восемьдесят": 80,
    "восьмидесятый": 80,
    "девяносто": 90,
    "девяностый": 90,
}
_HUNDREDS: dict[str, int] = {
    "сто": 100,
    "сотый": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_NUMERALS: dict[str, int] = {**_UNITS, **_TENS, **_HUNDREDS}


def words_to_numbers(text: str) -> str:
    """Numerals written out in words become digits: «дом семь» → «дом 7», «наряд четырнадцать
    двести семнадцать» → «наряд 14 217». Neighbouring words join into one number only while
    they can be its parts (hundreds, then tens, then units); anything else starts a new one.
    The text must already be normalized."""
    out: list[str] = []
    current: int | None = None
    for word in text.split():
        value = _NUMERALS.get(word)
        if value is None:
            if current is not None:
                out.append(str(current))
                current = None
            out.append(word)
            continue
        if current is None:
            current = value
        elif word in _HUNDREDS or (word in _TENS and current % 100 != 0) or current % 10 != 0:
            out.append(str(current))  # a part that cannot continue the number starts a new one
            current = value
        else:
            current += value
    if current is not None:
        out.append(str(current))
    return " ".join(out)


# Words that carry no meaning of their own when a phrase is compared with a card: form words,
# and the parts of an address or of a person that every incident description repeats.
_EMPTY_WORDS = frozenset(
    """
    адрес август апрель везде вокруг всего всем всех говорит город декабрь деревня дома доме домов
    женщина здание значит квартал квартира квартире квартиры который которая которое
    людей люди мужчина назад напротив находится начал начало никого около округ очень платформа
    подъезд подъезда подъезде поселение поселок после посмотрите почти пришел проезд ребенок
    район рядом сегодня сейчас сказал сколько слышно смотрите снова совсем сообщаю сообщили
    корпус строение улица улице улицы участок через этаж этаже этажа январь
    """.split()
)
# A word is compared by its beginning: Russian inflection changes the tail, «течёт» and «течи»
# are the same thing to a dispatcher.
_STEM_LENGTH = 6
_MIN_WORD = 4


def content_words(text: str | None, min_length: int = _MIN_WORD) -> list[str]:
    """Words of the phrase that carry meaning: long enough, not a form word, not a number."""
    return [
        word
        for word in normalize_text(text).split()
        if len(word) >= min_length and word not in _EMPTY_WORDS and not word.isdigit()
    ]


def stem(word: str) -> str:
    """The beginning of a word, the part inflection leaves alone."""
    return word[:_STEM_LENGTH]


def stems(text: str | None, min_length: int = _MIN_WORD) -> set[str]:
    """Beginnings of the meaningful words, for comparing phrases across inflection."""
    return {stem(word) for word in content_words(text, min_length)}


def has_number(text: str | None) -> bool:
    """A number is named in the phrase, in digits or in words."""
    normalized = normalize_text(text)
    return bool(_DIGIT.search(normalized) or _NUMERALS.keys() & set(normalized.split()))


def normalize_house(text: str | None) -> str:
    """«21», «д. 21», «21А», «21 а», «двадцать один» → «21а» / «21»."""
    value = words_to_numbers(normalize_text(text))
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
