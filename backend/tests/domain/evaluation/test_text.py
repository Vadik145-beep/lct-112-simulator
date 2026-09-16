"""Text normalization: case, «ё», punctuation, street types, keywords, topics."""

import pytest

from app.domain.evaluation.text import (
    contains_keyword,
    detect_topics,
    keyword_coverage,
    normalize_house,
    normalize_street,
    normalize_text,
    street_ratio,
    streets_match,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Задымление, МУСОРОПРОВОД!", "задымление мусоропровод"),
        ("Ёлки-палки ещё", "елки палки еще"),
        ("  два   пробела ", "два пробела"),
        (None, ""),
    ],
)
def test_normalize_text(text: str | None, expected: str) -> None:
    assert normalize_text(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ул. Берзарина", "берзарина улица"),
        ("улица Берзарина", "берзарина улица"),
        ("Улица  БЕРЗАРИНА", "берзарина улица"),
        ("Ленинский пр-т", "ленинский проспект"),
        ("просп. Мира", "мира проспект"),
        ("Б. Сухаревский пер.", "б сухаревский переулок"),
        ("Дмитровское ш.", "дмитровское шоссе"),
        ("бульвар Маршала Рокоссовского", "маршала рокоссовского бульвар"),
        ("Коломенская наб.", "коломенская набережная"),
    ],
)
def test_normalize_street(text: str, expected: str) -> None:
    assert normalize_street(text) == expected


@pytest.mark.parametrize(
    ("a", "b", "match"),
    [
        ("ул. Берзарина", "улица Берзарина", True),
        ("улица берзарина", "Улица Берзарина", True),
        ("Берзарина", "улица Берзарина", True),
        ("Берзарино", "улица Берзарина", False),
        ("улица Вавилова", "улица Вавилова", True),
        ("Ленинский проспект", "Ленинская улица", False),
        ("Тюменская улица", "ул. Тюменская", True),
        ("", "улица Берзарина", False),
        (None, "улица Берзарина", False),
    ],
)
def test_streets_match(a: str | None, b: str, match: bool) -> None:
    assert streets_match(a, b) is match
    if a:
        assert 0 <= street_ratio(a, b) <= 100


@pytest.mark.parametrize(
    ("text", "expected"),
    [("21", "21"), ("д. 21", "21"), ("дом 21А", "21а"), ("21 а", "21а"), (None, "")],
)
def test_normalize_house(text: str | None, expected: str) -> None:
    assert normalize_house(text) == expected


def test_contains_keyword_allows_inflection_and_typos() -> None:
    text = "Задымление мусоропровода, открытого пламени нет, пострадавших нет"
    assert contains_keyword(text, "мусоропровод")
    assert contains_keyword(text, "открытого пламени")
    assert contains_keyword(text, "Пострадавших нет")
    assert contains_keyword(text, "мусаропровод")  # one-letter typo
    assert not contains_keyword(text, "лифт")
    assert not contains_keyword("", "лифт")


def test_keyword_coverage_splits_found_and_missing() -> None:
    found, missing = keyword_coverage("свист трубы на кухне", ["свист", "кухне", "запах газа"])
    assert found == ["свист", "кухне"]
    assert missing == ["запах газа"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Скажите адрес, где это произошло?", ["address"]),
        ("Есть пострадавшие?", ["injured"]),
        ("Как вас зовут? Телефон для связи?", ["caller_name", "callback_phone"]),
        ("Какой подъезд и этаж?", ["entrance_floor_code"]),
        ("Это Москва или область?", ["region"]),
        ("Приезжайте скорее!", []),
        ("", []),
    ],
)
def test_detect_topics(text: str, expected: list[str]) -> None:
    assert detect_topics(text) == expected
