"""«Проверка данных» (issue #35): planted operator mistakes, flags of the dispatcher, the
component, its weight and the two detectors."""

from __future__ import annotations

from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.data_check import values_match
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from tests.domain.evaluation.helpers import at, card_scenario, grammar_ok, perfect_card_attempt

HOUSE = {"field": "address.house", "wrong_value": "23", "correct_value": "21", "hint_level": 2}
SERVICE = {
    "field": "services",
    "wrong_value": "+moslift",
    "correct_value": "-moslift",
    "hint_level": 1,
    "correct_label": "Мослифт",
}
CLASSIC = {
    "decision": 30,
    "time": 20,
    "status_chain": 20,
    "comments": 15,
    "typical_errors": 10,
    "grammar": 5,
}


def planted(*errors: dict) -> CardResponseScenario:
    scenario = card_scenario()
    body = scenario.model_dump()
    body["card"]["address"]["house"] = "23"
    body["injected_errors"] = list(errors)
    return CardResponseScenario.model_validate(body)


def flagged(scenario: CardResponseScenario, *flags: tuple[str, str]) -> CardResponseAttempt:
    attempt = perfect_card_attempt(scenario).model_dump()
    attempt["flagged_fields"] = [
        {"field": f, "corrected_value": v, "at": at(10 + i)} for i, (f, v) in enumerate(flags)
    ]
    return CardResponseAttempt.model_validate(attempt)


def test_all_planted_errors_found_gives_full_points() -> None:
    scenario = planted(HOUSE, SERVICE)
    attempt = flagged(scenario, ("address.house", "д. 21"), ("services", "-moslift"))
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    check = result.components["data_check"]
    assert check.max == 16  # 20 of 120, normalized with the six classic components
    assert sum(c.max for c in result.components.values()) == 100
    assert check.score == check.max
    assert [i["verdict"] for i in check.items] == ["found", "found"]
    assert result.total == 100
    assert not {e.code for e in result.errors} & {"error_missed", "false_alarm"}


def test_one_of_two_missed_gives_half_and_the_detector() -> None:
    scenario = planted(HOUSE, SERVICE)
    attempt = flagged(scenario, ("address.house", "21"))
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    check = result.components["data_check"]
    assert check.score == round(check.max / 2, 1)
    assert [i["verdict"] for i in check.items] == ["found", "missed"]
    errors = {e.code: e for e in result.errors}
    assert "error_missed" in errors
    assert "Мослифт" in errors["error_missed"].explanation
    assert "false_alarm" not in errors


def test_false_alarm_costs_half_a_share_and_fires_the_detector() -> None:
    scenario = planted(HOUSE)
    attempt = flagged(scenario, ("address.house", "21"), ("caller.phone", "+7 916 000-00-00"))
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    check = result.components["data_check"]
    assert check.score == round(check.max * 0.5, 1)
    assert check.items[-1]["verdict"] == "false_alarm"
    errors = {e.code: e for e in result.errors}
    assert "false_alarm" in errors
    assert "Телефон заявителя" in errors["false_alarm"].explanation
    assert "error_missed" not in errors


def test_right_field_wrong_correction_gives_a_partial_score() -> None:
    scenario = planted(HOUSE)
    attempt = flagged(scenario, ("address.house", "25"))
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    check = result.components["data_check"]
    assert check.score == round(check.max * 0.5, 1)
    assert check.items[0]["verdict"] == "wrong_correction"
    assert check.items[0]["corrected_value"] == "25"
    assert not {e.code for e in result.errors} & {"error_missed", "false_alarm"}


def test_scenario_without_planted_errors_has_no_component() -> None:
    scenario = card_scenario()
    result = evaluate_card_response(scenario, perfect_card_attempt(scenario), grammar=grammar_ok())
    assert "data_check" not in result.components
    assert result.total == 100
    assert {k: c.max for k, c in result.components.items()} == CLASSIC


def test_classic_weights_apply_to_the_remaining_components() -> None:
    """A lesson created with the six classic weights: on a card with planted errors the seventh
    component joins with its default weight and everything is normalized to 100."""
    scenario = planted(HOUSE)
    attempt = flagged(scenario, ("address.house", "21"))
    result = evaluate_card_response(scenario, attempt, weights=CLASSIC, grammar=grammar_ok())
    maxima = {k: c.max for k, c in result.components.items()}
    assert sum(maxima.values()) == 100
    assert maxima["data_check"] == 16
    assert maxima["decision"] == 25 + 3  # 30 of 120 → 25, the remainder of rounding
    assert result.total == 100


def test_values_match_by_field_kind() -> None:
    assert values_match("address.street", "улица Берзарина", "ул. Берзарина")
    assert not values_match("address.street", "улица Берзарина", "Ленинский проспект")
    assert values_match("address.house", "21А", "д. 21 а")
    assert values_match("flags.injured", "false", "нет")
    assert not values_match("flags.injured", "true", "нет")
    assert values_match("incident_type", "14.2.3.0", "14.2.3.0")
    assert values_match(
        "incident_type",
        "14.2.3.0",
        "Течь: прорыв воды в жилом доме",
        label="Течь (прорыв воды) в жилом доме",
    )
    assert values_match("services", "-moslift", "-MOSLIFT")
    assert values_match("services", "-moslift", "- Мослифт", label="Мослифт")
    assert not values_match("services", "-moslift", "+moslift")
    assert values_match("caller.phone", "+7 916 402-17-55", "7 (916) 402 17 55")
    assert not values_match("caller.phone", "+7 916 402-17-55", "+7 916 402-17-56")
