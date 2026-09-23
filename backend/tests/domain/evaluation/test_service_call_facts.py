"""How a dispatcher's report to the service officer is read: numbers said aloud, the incident
named in the card's own words, and the phrases that must NOT count as a fact
(``app.domain.evaluation.service_call``, ``app.domain.evaluation.text``)."""

from __future__ import annotations

import pytest

from app.domain.evaluation.schemas import Card
from app.domain.evaluation.service_call import address_named, incident_named
from app.domain.evaluation.text import (
    detect_service_facts,
    has_number,
    normalize_house,
    words_to_numbers,
)


def card() -> Card:
    return Card.model_validate(
        {
            "incident_type": "13.2.3.0",
            "signs": ["Запах газа в помещении", "Дом частный"],
            "address": {"street": "улица Полевая", "house": "7", "city": "посёлок Кокошкино"},
            "description": "В частном доме запах газа от трубы на вводе, слышен шум в трубе.",
        }
    )


# --- numbers said aloud ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "expected"),
    [
        ("дом семь", "дом 7"),
        ("дом двадцать один", "дом 21"),
        ("дом сто тринадцать", "дом 113"),
        ("дом восемьдесят один", "дом 81"),
        ("наряд четырнадцать двести семнадцать", "наряд 14 217"),
        ("квартира пятая", "квартира 5"),
        ("подъезд первый этаж второй", "подъезд 1 этаж 2"),
        ("улица полевая", "улица полевая"),
        ("дом 81", "дом 81"),
    ],
)
def test_numerals_in_words_become_digits(said: str, expected: str) -> None:
    assert words_to_numbers(said) == expected


def test_a_house_reads_the_same_whether_it_is_said_or_written() -> None:
    assert normalize_house("дом семь") == normalize_house("д. 7") == "7"
    assert normalize_house("сто тринадцать") == "113"


def test_a_squad_number_counts_when_it_is_said_in_words() -> None:
    assert has_number("наряд четырнадцать двести семнадцать")
    assert has_number("наряд 14-217")
    assert not has_number("наряд выслали")


def test_the_squad_number_is_missing_without_a_number() -> None:
    assert "order_number" in detect_service_facts("наряд 14-217")
    assert "order_number" in detect_service_facts("наряд четырнадцать двести семнадцать")


# --- the address -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "улица Полевая, дом 7",
        "улица Полевая, дом семь",
        "Полевая семь",
        "ул. Полевая, д. 7",
        "Палевая дом 7",  # a typo in the street still names it
    ],
)
def test_the_address_is_recognised_however_it_is_said(said: str) -> None:
    assert address_named(card(), said)


@pytest.mark.parametrize(
    "said",
    [
        "улица Полевая, дом 99",  # right street, wrong house
        "улица Ленина, дом 7",  # wrong street
        "Записываю адрес",  # the word «адрес» is not an address
        "Пострадавших нет. Наряд 14-217.",
    ],
)
def test_a_phrase_that_does_not_name_the_address_is_not_one(said: str) -> None:
    assert not address_named(card(), said)


def test_the_word_that_carries_the_street_is_enough() -> None:
    named = Card.model_validate(
        {
            "incident_type": "14.2.3.0",
            "address": {"street": "улица Героев Панфиловцев", "house": "27"},
            "description": "Течёт стояк.",
        }
    )
    assert address_named(named, "Панфиловцев двадцать семь")
    assert not address_named(named, "Героев двадцать семь")  # the leading word names no street


# --- the incident ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    [
        "Запах газа в помещении",  # a sign of the card
        "В доме запах газа от трубы",  # the caller's own words
        "Сильный запах газа, дом частный",  # the keyword table
        "Шум в трубе на вводе",  # only the card knows this one
    ],
)
def test_the_incident_is_recognised_in_the_words_of_the_card(said: str) -> None:
    assert incident_named(card(), said)


def test_a_short_word_is_not_matched_across_its_endings() -> None:
    """The limit of comparing by the beginning of a word: «газа» and «газом» are too short to
    share one. Such wordings are covered by the keyword table instead, and «запах газа» is in
    it — a phrase built only of short words and none of the keywords is not recognised."""
    assert not incident_named(card(), "Тянет газом")


@pytest.mark.parametrize(
    "said",
    [
        "улица Полевая, дом 7",  # the address is not the incident
        "Пострадавших нет",  # nor is the casualty count
        "Наряд 14-217",
        "Код домофона 5В, встретят у калитки",
        "Алло, добрый день",
    ],
)
def test_naming_another_fact_is_not_naming_the_incident(said: str) -> None:
    assert not incident_named(card(), said)


def test_the_incident_is_recognised_where_the_keyword_table_is_silent() -> None:
    """«течёт» is not the keyword «течь»; the card's own description carries it."""
    leak = Card.model_validate(
        {
            "incident_type": "14.2.3.0",
            "signs": ["Прорыв воды (холодной, горячей воды)"],
            "address": {"street": "улица Свободы", "house": "42"},
            "description": "На лестничной клетке 3 этажа течёт стояк холодной воды.",
        }
    )
    assert incident_named(leak, "На третьем этаже течёт стояк")


# --- casualties ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "said",
    ["Пострадавших нет", "Людей не задело", "б/п", "никого не задело", "есть пострадавшие, двое"],
)
def test_the_casualty_count_is_recognised_as_it_is_spoken(said: str) -> None:
    assert "injured" in detect_service_facts(said)
