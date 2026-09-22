"""«Приём вызова»: survey card, flags and services, address, topics, description, time."""

from __future__ import annotations

import pytest

from app.domain.evaluation import evaluate_sync
from app.domain.evaluation.call_intake import evaluate_call_intake
from app.domain.evaluation.schemas import Address, DialogTurn
from tests.domain.evaluation.helpers import (
    call_scenario,
    grammar_down,
    grammar_errors,
    grammar_ok,
    load,
    perfect_call_attempt,
)


def test_perfect_attempt_scores_100() -> None:
    scenario = call_scenario()
    result = evaluate_call_intake(scenario, perfect_call_attempt(scenario), grammar=grammar_ok())
    assert result.total == 100
    assert result.passed
    assert result.errors == []
    assert {k: c.score for k, c in result.components.items()} == {
        "survey_card": 25,
        "flags_services": 10,
        "address": 15,
        "required_topics": 15,
        "description": 10,
        "time": 10,
        "typical_errors": 5,
        "grammar": 10,
    }


@pytest.mark.parametrize(
    ("incident_type", "expected", "note"),
    [
        ("1.5.6.2", 25, "тип происшествия верен"),
        ("1.5.6.1", 15, "верный тип, неверен нижний уровень"),  # «открытое пламя» вместо «дым»
        ("1.5.16.0", 5, "верна только группа"),  # другой признак в той же группе
        ("1.3.3.0", 5, "верна только группа"),
        ("13.2.4.0", 0, "тип происшествия не совпал"),
    ],
)
def test_survey_card_by_type_code(incident_type: str, expected: int, note: str) -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.incident_type = incident_type
    component = evaluate_call_intake(scenario, attempt).components["survey_card"]
    assert component.score == expected
    assert component.items[0]["note"] == note


def test_survey_card_by_signs_path_when_no_code() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.incident_type = None
    attempt.card.signs_path = ["Жилой дом", "Мусоропровод", "Открытое пламя"]
    component = evaluate_call_intake(scenario, attempt).components["survey_card"]
    assert component.score == 15
    attempt.card.signs_path = ["жилой дом", "мусоропровод", "дым"]
    assert evaluate_call_intake(scenario, attempt).components["survey_card"].score == 25


def test_flags_and_services() -> None:
    scenario = call_scenario("call_30-2_naezd_na_peshehoda")
    attempt = perfect_call_attempt(scenario)
    attempt.card.flags = {"injured": False, "road_closed": False}  # forgot the injured flag
    attempt.card.services = [s for s in attempt.card.services if s not in {"103", "cemp"}]
    component = evaluate_call_intake(scenario, attempt).components["flags_services"]
    assert component.items[0]["flags_wrong"] == ["injured"]
    assert component.items[1]["missing"] == ["103", "cemp"]
    expected = len(scenario.reference_card.expected_services)
    jaccard = (expected - 2) / expected
    assert component.score == round(5 * 0.5 + 5 * jaccard, 1)


@pytest.mark.parametrize(
    ("street", "house", "expected_fraction"),
    [
        ("улица Берзарина", "21", 1.0),
        ("ул. Берзарина", "д. 21", 1.0),
        ("Берзарина", "21", 1.0),
        ("Берзарино", "21", 1 - 3 / 9),
        ("улица Берзарина", "12", 1 - 2 / 9),
        (None, None, 4 / 9),  # only building, entrance, floor, code from the reference
    ],
)
def test_address_parts(street: str | None, house: str | None, expected_fraction: float) -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.address.street = street
    attempt.card.address.house = house
    component = evaluate_call_intake(scenario, attempt).components["address"]
    assert component.score == round(15 * expected_fraction, 1)


def test_address_optional_parts_only_when_the_reference_has_them() -> None:
    scenario = call_scenario("call_30-2_naezd_na_peshehoda")  # street + descriptive, no house
    attempt = perfect_call_attempt(scenario)
    attempt.card.address.entrance = "7"  # extra data is not penalized
    component = evaluate_call_intake(scenario, attempt).components["address"]
    assert component.score == 15
    parts = {i["part"] for i in component.items}
    assert "house" not in parts
    assert "entrance" not in parts


def test_descriptive_address_without_a_street() -> None:
    scenario = call_scenario("call_1-3_rebenok_velosiped_volzhskiy")
    attempt = perfect_call_attempt(scenario)
    attempt.card.address.street = "Карла Маркса"
    attempt.card.address.region = "Волгоградская обл."
    attempt.card.address.city = "г. Волжский"
    component = evaluate_call_intake(scenario, attempt).components["address"]
    assert component.score == 15


def test_required_topics_are_proportional() -> None:
    scenario = call_scenario()  # 7 required topics
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = [t for t in attempt.dialog if not set(t.topics) & {"danger", "callback_phone"}]
    component = evaluate_call_intake(scenario, attempt).components["required_topics"]
    assert component.items[0]["missing"] == ["danger", "callback_phone"]
    assert component.score == round(15 * 5 / 7, 1)


def test_topics_are_inferred_from_text_when_not_labelled() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = [
        DialogTurn(role="caller", text=scenario.caller.opening),
        DialogTurn(role="operator", text="Что случилось? Назовите адрес."),
        DialogTurn(role="operator", text="Какой подъезд и этаж? Код домофона?"),
        DialogTurn(role="operator", text="Пострадавшие есть? Огонь видите?"),
        DialogTurn(role="operator", text="Как вас зовут и телефон для связи?"),
    ]
    component = evaluate_call_intake(scenario, attempt).components["required_topics"]
    assert component.items[0]["missing"] == []
    assert component.score == 15


def test_description_keywords_and_similarity() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.description = "Дым из мусоропровода на 7 этаже, пламени нет, никто не пострадал"
    component = evaluate_call_intake(scenario, attempt).components["description"]
    assert "мусоропровод" in component.items[0]["keywords_found"]
    assert "7 этаж" in component.items[0]["keywords_found"]
    assert 0 < component.score < 10
    attempt.card.description = "Кот на дереве"
    assert evaluate_call_intake(scenario, attempt).components["description"].score == 0


@pytest.mark.parametrize(("seconds", "expected"), [(30, 10), (60, 10), (90, 5), (120, 0)])
def test_time_component(seconds: int, expected: int) -> None:
    scenario = call_scenario()  # norm 60 s
    attempt = perfect_call_attempt(scenario, seconds=seconds)
    assert evaluate_call_intake(scenario, attempt).components["time"].score == expected


def test_grammar_two_points_per_error_and_renormalization() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    assert (
        evaluate_call_intake(scenario, attempt, grammar=grammar_errors(3))
        .components["grammar"]
        .score
        == 4
    )
    assert (
        evaluate_call_intake(scenario, attempt, grammar=grammar_errors(6))
        .components["grammar"]
        .score
        == 0
    )
    attempt.card.address.house = "12"  # address 15 × 7/9 = 11.7
    with_grammar = evaluate_call_intake(scenario, attempt, grammar=grammar_ok())
    without = evaluate_call_intake(scenario, attempt, grammar=grammar_down())
    assert without.components["grammar"].status == "not_checked"
    assert with_grammar.total == round(96.7)
    assert without.total == round(86.7 / 90 * 100)


def test_dropped_call_scenario_with_the_mark() -> None:
    scenario = call_scenario("call_2-2_skandal_sryv_zvonka")
    attempt = perfect_call_attempt(scenario)
    result = evaluate_call_intake(scenario, attempt, grammar=grammar_ok())
    assert result.total == 100
    attempt.call_dropped_marked = False
    result = evaluate_call_intake(scenario, attempt, grammar=grammar_ok())
    assert [e.code for e in result.errors] == ["no_call_dropped_mark"]
    assert result.components["typical_errors"].score == 3


def test_session_weights_without_grammar() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    weights = {
        "survey_card": 30,
        "flags_services": 10,
        "address": 20,
        "required_topics": 15,
        "description": 10,
        "time": 10,
        "typical_errors": 5,
        "grammar": 0,
    }
    result = evaluate_call_intake(scenario, attempt, weights=weights, grammar=grammar_errors(5))
    assert result.components["grammar"].status == "disabled"
    assert result.components["survey_card"].max == 30
    assert result.total == 100


def test_evaluate_sync_accepts_raw_dictionaries() -> None:
    body = load("call_31-3_svist_gazovoy_truby")
    scenario = call_scenario("call_31-3_svist_gazovoy_truby")
    attempt = perfect_call_attempt(scenario).model_dump(mode="json")
    result = evaluate_sync(body, attempt, grammar=grammar_ok())
    assert result.mode == "call_intake"
    assert result.total == 100
    assert result.to_dict()["components"]["address"]["score"] == 15


# --- blocking rules of issues #69 and #70 -----------------------------------------------------


def test_cloud_caller_speech_does_not_count_as_a_question_asked() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = [
        DialogTurn(role="operator", text="Расскажите, что случилось?", method="cloud"),
        # The caller volunteers the address and the danger: nobody asked.
        DialogTurn(role="caller", text="Улица Берзарина, дом 21, дым идёт", method="cloud"),
    ]
    component = evaluate_call_intake(scenario, attempt).components["required_topics"]
    assert component.items[0]["covered"] == ["what_happened"]
    # A local mode labels the caller's reply with the topic it answers: that still counts.
    attempt.dialog = [
        DialogTurn(role="operator", text="Куда ехать?", method="select"),
        DialogTurn(role="caller", text="Берзарина, 21", topics=["address"], method="select"),
    ]
    component = evaluate_call_intake(scenario, attempt).components["required_topics"]
    assert component.items[0]["covered"] == ["address"]


def test_fewer_than_half_of_the_questions_fails_the_attempt() -> None:
    scenario = call_scenario()  # 7 required topics → at least 4 must be asked
    attempt = perfect_call_attempt(scenario)
    asked = {"what_happened", "address", "injured"}
    attempt.dialog = [t for t in attempt.dialog if set(t.topics) <= asked]
    result = evaluate_call_intake(scenario, attempt, grammar=grammar_ok())
    error = next(e for e in result.errors if e.code == "questions_not_asked")
    assert error.critical
    assert error.explanation == "Незачёт: задано 3 из 7 обязательных вопросов (нужно не меньше 4)."
    assert result.total >= 70  # the card itself is perfect …
    assert not result.passed  # … but the attempt fails anyway
    assert result.components["typical_errors"].score == 5 - 3

    # Exactly half is enough; the share is a setting of the scenario; 0 disables the rule.
    attempt = perfect_call_attempt(scenario)
    attempt.dialog = [t for t in attempt.dialog if set(t.topics) <= asked | {"danger"}]
    assert evaluate_call_intake(scenario, attempt, grammar=grammar_ok()).passed
    scenario.min_questions_share = 0.8  # 6 of 7
    assert not evaluate_call_intake(scenario, attempt, grammar=grammar_ok()).passed
    scenario.min_questions_share = 0
    assert evaluate_call_intake(scenario, attempt, grammar=grammar_ok()).passed


def test_empty_card_scores_zero_where_there_is_nothing_to_check() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.incident_type = None
    attempt.card.signs_path = []
    attempt.card.address = Address()
    attempt.card.services = []
    attempt.card.flags = {}
    attempt.card.description = "пропуск"
    result = evaluate_call_intake(scenario, attempt, grammar=grammar_ok())
    scores = {k: c.score for k, c in result.components.items()}
    assert scores == {
        "survey_card": 0,
        "flags_services": 0,
        "address": 0,
        "required_topics": 15,  # the conversation was fine
        "description": 0,
        "time": 0,
        "typical_errors": 0,  # card_empty costs the whole component
        "grammar": 0,
    }
    assert result.components["grammar"].status == "checked"
    assert result.methods["grammar"] == "not_checked"
    assert result.total == 15
    error = next(e for e in result.errors if e.code == "card_empty")
    assert error.critical
    assert not result.passed


def test_one_word_description_zeroes_only_the_text_components() -> None:
    scenario = call_scenario()
    attempt = perfect_call_attempt(scenario)
    attempt.card.description = "пожар"
    result = evaluate_call_intake(scenario, attempt, grammar=grammar_errors(0))
    assert result.components["description"].score == 0
    assert result.components["grammar"].score == 0
    assert result.components["time"].score == 10
    assert result.components["flags_services"].score == 10
    assert "card_empty" not in {e.code for e in result.errors}
    assert result.passed
    attempt.card.description = "Дым из мусоропровода"
    assert (
        evaluate_call_intake(scenario, attempt, grammar=grammar_ok()).components["grammar"].score
        == 10
    )
