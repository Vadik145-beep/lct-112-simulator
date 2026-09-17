"""Incident type for a situation text without a model: word-stem overlap between the
situation and the classifier row (signs, final title, statistics group, hints), with a small
synonym map from everyday words of the tickets to the classifier's vocabulary.

Good enough for a *draft* (the teacher reviews every generated scenario); the generation
model, when present, picks the type itself and this module only checks that the code exists.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.evaluation.text import normalize_text

STEM_LENGTH = 6
SHORT_STEM_LENGTH = 4  # second pass when nothing matched with the long stems
MIN_WORD = 3
# Words that appear in most rows and separate nothing.
STOP = {
    "на",
    "в",
    "и",
    "с",
    "без",
    "не",
    "или",
    "для",
    "от",
    "по",
    "при",
    "из",
    "до",
    "за",
    "у",
    "к",
    "о",
    "об",
    "под",
    "над",
    "то",
    "что",
    "как",
    "все",
    "другой",
    "другое",
    "прочее",
    "прочие",
    "объект",
    "объекты",
    "человек",
    "люди",
    "нет",
    "есть",
    "лет",
    "около",
    "рядом",
    "него",
    "его",
    "ее",
    "их",
    "сам",
    "сама",
    "также",
}

# Ticket wording → classifier wording. Values are added to the situation's stems.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "возгорание": ("пожар", "открытое", "пламя"),
    "горит": ("пожар", "открытое", "пламя"),
    "горят": ("пожар", "открытое", "пламя"),
    "пламя": ("пожар", "открытое"),
    "дым": ("задымление",),
    "дыма": ("задымление", "дым"),
    "задымление": ("дым",),
    "контейнер": ("мусор",),
    "контейнера": ("мусор",),
    "дерутся": ("драка",),
    "драка": ("драка",),
    "прутами": ("драка", "оружие"),
    "палками": ("драка",),
    "упал": ("травма",),
    "упала": ("травма",),
    "отек": ("травма",),
    "ноги": ("травма",),
    "руки": ("травма",),
    "перелом": ("травма",),
    "велосипеда": ("травма", "велосипед"),
    "поругался": ("скандал", "продавцом"),
    "продавцом": ("продавцом", "торговли", "нарушение", "правил"),
    "музыка": ("тишины", "нарушение", "шум"),
    "громко": ("тишины", "нарушение", "шум"),
    "автомашины": ("автомашина", "транспорт"),
    "автомашина": ("транспорт",),
    "машина": ("автомашина", "транспорт"),
    "машину": ("автомашина", "транспорт"),
    "машине": ("автомашина", "транспорт"),
    "воду": ("вода", "водоем", "утопление", "падение"),
    "нырнул": ("вода", "водоем", "утопление", "купание"),
    "крыша": ("кровля", "частный", "дом"),
    "частного": ("частный",),
    "заблокирован": ("зажат", "деблокирование", "дтп"),
    "бревно": ("груз", "дтп", "падение"),
    "грузовика": ("грузовой", "транспорт", "дтп"),
    "балкон": ("жилой", "дом", "балкон"),
    "окно": ("жилой", "дом", "квартира"),
    "окна": ("жилой", "дом", "квартира"),
    "плохо": ("медицинской", "помощи", "скорой", "неотложной", "заболевание"),
    "сознания": ("медицинской", "помощи", "скорой", "без", "сознания"),
    "сознание": ("медицинской", "помощи", "скорой", "без", "сознания"),
    "препараты": ("отравление", "лекарствами", "медицинской", "помощи"),
    "лекарственные": ("отравление", "лекарствами", "медицинской"),
    "снотворного": ("отравление", "лекарствами", "медицинской"),
    "выпила": ("отравление", "медицинской", "помощи"),
    "задыхается": ("медицинской", "помощи", "скорой", "затруднение", "дыхания"),
    "астма": ("медицинской", "помощи", "скорой", "затруднение", "дыхания"),
    "открыть": ("вскрытие", "двери", "дверь"),
    "дверь": ("вскрытие", "двери"),
    "инвалид": ("недееспособный", "пожилой"),
    "ножом": ("ножевое", "ранение"),
    "кровотечение": ("ранение", "травма", "медицинской"),
    "затащили": ("похищение", "похищен", "скрылись", "автомашине"),
    "скрылись": ("похищение", "скрылись", "автомашине"),
    "лес": ("лес",),
    "леса": ("лес",),
    "столб": ("задымление", "дым", "улице"),
    "поселка": ("улице", "частный"),
    "трещина": ("трещина", "угроза", "обрушения", "многоквартирный", "зданиях"),
    "стены": ("трещина", "угроза", "обрушения", "многоквартирный", "зданиях"),
    "коробка": ("подозрительный", "предмет", "сумка", "мешок"),
    "скотчем": ("подозрительный", "предмет", "сумка", "мешок"),
    "тикает": ("подозрительный", "предмет", "сумка", "мешок"),
    "подозрительный": ("подозрительный", "предмет"),
    "дтп": ("дтп",),
    "б/п": ("без", "пострадавших"),
    "б/р": ("без", "разлива"),
    "табло": ("конструкции", "элементы", "угроза", "обрушения", "падения"),
    "крепления": ("конструкции", "элементы", "угроза", "обрушения"),
    "плитка": ("угроза", "обрушения", "элементы", "конструкции"),
    "ремонт": ("нарушение",),
    "мусоропровода": ("мусоропровод",),
    "ребенок": ("ребенок", "несовершеннолетний"),
    "ребёнок": ("ребенок", "несовершеннолетний"),
    "подросток": ("ребенок", "несовершеннолетний"),
    "жену": ("человек",),
    "газом": ("газа", "запах"),
    "газа": ("запах", "газа"),
    "пахнет": ("запах",),
    "дворе": ("двор", "улица"),
    "парке": ("парк",),
    "дорожке": ("улица",),
    "пояснил": ("подозрительные", "посторонние", "граждане"),
    "присутствия": ("подозрительные", "посторонние", "граждане"),
    "ждет": ("подозрительные", "посторонние", "граждане"),
    "вокзале": ("вокзал", "транспортных", "объектах"),
    "паркинге": ("автостоянка", "гараж"),
    "паркинг": ("автостоянка", "гараж"),
    "парковке": ("автостоянка", "улица"),
    "стоянке": ("автостоянка",),
    "гараже": ("гараж",),
    "звонит": (),
    "звонок": (),
    "метро": ("метро", "метрополитен"),
}


@dataclass(frozen=True)
class TypeCandidate:
    code: str
    score: float
    title: str


SYNONYM_WEIGHT = 0.6
ROW_SIZE_NORM = 10  # a stem the synonym map added counts at least this much
_ABBREVIATIONS = {"б/п": "без пострадавших", "б/р": "без разлива", "дтп": "дтп"}


def _expand(text: str) -> str:
    lowered = text.lower()
    for short, full in _ABBREVIATIONS.items():
        lowered = lowered.replace(short, f" {full} ")
    return lowered


def stems(text: str, length: int = STEM_LENGTH) -> set[str]:
    return set(weighted_stems(text, length))


def weighted_stems(text: str, length: int = STEM_LENGTH) -> dict[str, bool]:
    """Stem → whether it came from the synonym map (a deliberate mapping, weighted higher)."""
    words = [
        w for w in normalize_text(_expand(text)).split() if len(w) >= MIN_WORD and w not in STOP
    ]
    result: dict[str, bool] = {w[:length]: False for w in words}
    for word in words:
        for extra in SYNONYMS.get(word, ()):
            result[extra[:length]] = True
    return result


def _row_text(row: Mapping) -> str:
    return " ".join(
        str(row.get(key) or "")
        for key in ("sign1", "sign2", "sign3", "final_title", "stat_group", "hints")
    )


def rank_types(
    situation: str,
    rows: Iterable[Mapping],
    *,
    injured: bool | None = None,
    indoors: bool | None = None,
    limit: int = 5,
) -> list[TypeCandidate]:
    """Best matching classifier rows for a situation. ``injured`` and ``indoors`` (address has
    entrance, floor or apartment) nudge fire and traffic rows towards the right leaf. When the
    long stems match nothing, a second pass uses short ones so some row is always suggested."""
    rows = list(rows)
    for length in (STEM_LENGTH, SHORT_STEM_LENGTH):
        ranked = _rank(
            situation, rows, injured=injured, indoors=indoors, limit=limit, length=length
        )
        if ranked:
            return ranked
    return []


def _rank(
    situation: str,
    rows: list[Mapping],
    *,
    injured: bool | None,
    indoors: bool | None,
    limit: int,
    length: int,
) -> list[TypeCandidate]:
    situation_stems = weighted_stems(situation, length)
    if injured is True:
        situation_stems.setdefault("постр", False)
    if indoors is True:
        situation_stems.update({"жилой": False, "дом": False})
    elif indoors is False:
        situation_stems.setdefault("улице", False)

    # Rare stems separate rows better: weight = 1 / rows containing the stem (smoothed).
    frequency: dict[str, int] = {}
    row_stems: list[set[str]] = []
    for row in rows:
        own = stems(_row_text(row), length)
        row_stems.append(own)
        for stem in own:
            frequency[stem] = frequency.get(stem, 0) + 1
    total = max(len(rows), 1)

    scored: list[TypeCandidate] = []
    for row, own in zip(rows, row_stems, strict=True):
        common = set(situation_stems) & own
        if not common:
            continue
        score = 0.0
        for stem in common:
            weight = 1.0 / (1.0 + frequency[stem] * 10 / total)
            score += max(weight, SYNONYM_WEIGHT) if situation_stems[stem] else weight
        # A row about «Пострадавшие» when nobody is hurt loses a little; «без пострадавших»
        # does not count as injured.
        row_injured = bool(re.search(r"(?<!без )пострадавш", _row_text(row), re.IGNORECASE))
        if injured is False and row_injured:
            score -= 0.5
        if injured is True and row_injured:
            score += 0.3
        # Long enumerations («кровля крыша фасад балкон столб щит…») touch every situation:
        # normalize by the row's own size.
        score *= ROW_SIZE_NORM / (ROW_SIZE_NORM + len(own))
        # Fewer filled signs is a broader row; prefer the specific leaf when scores tie.
        specificity = sum(1 for k in ("sign2", "sign3") if row.get(k)) * 0.01
        scored.append(
            TypeCandidate(
                str(row["code"]), round(score + specificity, 4), row.get("final_title") or ""
            )
        )
    scored.sort(key=lambda c: (-c.score, c.code))
    return scored[:limit]
