"""«Звонки в службы» (issue #36): facts read from the dispatcher's turns, the component and
the ``service_not_informed`` detector; scenarios without reference calls keep the old totals."""

from __future__ import annotations

from datetime import timedelta

from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.evaluation.service_call import facts_from_dialog
from tests.domain.evaluation.helpers import (
    at,
    card_scenario,
    grammar_ok,
    perfect_card_attempt,
    perfect_report_calls,
)

REQUIRED = ["address", "incident_type", "injured", "order_number"]


def with_calls(name: str = "card_moek_1_net_otopleniya", **ref) -> CardResponseScenario:
    scenario = card_scenario(name)
    body = scenario.model_dump()
    body["reference"]["service_calls"] = [
        {"service": scenario.service, "required_facts": REQUIRED, "norm_seconds": 120, **ref}
    ]
    return CardResponseScenario.model_validate(body)


def turn(role: str, text: str, seconds: float, topics: list[str] | None = None) -> dict:
    return {"role": role, "text": text, "topics": topics or [], "at": at(seconds)}


def called(
    scenario: CardResponseScenario,
    dispatcher_phrases: list[str],
    *,
    seconds: float = 60,
    answered: bool = True,
    service: str | None = None,
) -> CardResponseAttempt:
    attempt = perfect_card_attempt(scenario).model_dump()
    dialog = [turn("caller", "Дежурный МОЭК, слушаю.", 30)]
    for i, phrase in enumerate(dispatcher_phrases):
        dialog.append(turn("operator", phrase, 31 + i * 5))
        dialog.append(turn("caller", "Принял.", 32 + i * 5))
    # The squad's reports stay: this test is about the dispatcher's own call.
    attempt["service_calls"] = [
        {
            "service": service or scenario.service,
            "started_at": at(30),
            "answered": answered,
            "ended_at": at(30 + seconds),
            "dialog": dialog,
            "facts_passed": [],
        },
        *perfect_report_calls(scenario),
    ]
    return CardResponseAttempt.model_validate(attempt)


FULL = [
    "Дежурный, передаю карточку: улица Молостовых, дом 10, корпус 1.",
    "Нет отопления в трёх домах, горячая вода есть.",
    "Пострадавших нет.",
    "Наряд МОЭК-4127, выезжайте.",
]


def test_facts_are_read_from_the_dispatcher_turns_only() -> None:
    scenario = with_calls()
    attempt = called(scenario, FULL)
    log = attempt.service_calls[0]
    assert facts_from_dialog(scenario.card, log.dialog) == REQUIRED
    # The officer naming the address does not count for the dispatcher.
    officer_only = CardResponseAttempt.model_validate(
        {
            **attempt.model_dump(),
            "service_calls": [
                {
                    **log.model_dump(),
                    "dialog": [turn("caller", "Улица Молостовых, дом 10? Пострадавшие?", 1)],
                }
            ],
        }
    )
    assert facts_from_dialog(scenario.card, officer_only.service_calls[0].dialog) == []


def test_address_needs_the_street_and_the_house_of_the_card() -> None:
    scenario = with_calls()
    only_word = called(scenario, ["Адрес передаю позже."])
    assert "address" not in facts_from_dialog(scenario.card, only_word.service_calls[0].dialog)
    wrong_house = called(scenario, ["Адрес: улица Молостовых, дом 12."])
    assert "address" not in facts_from_dialog(scenario.card, wrong_house.service_calls[0].dialog)
    right = called(scenario, ["Молостовых десять, дом 10 корпус 1, выезжайте."])
    assert "address" in facts_from_dialog(scenario.card, right.service_calls[0].dialog)


def test_order_number_needs_a_number() -> None:
    scenario = with_calls()
    no_number = called(scenario, ["Наряд оформлю позже."])
    assert "order_number" not in facts_from_dialog(scenario.card, no_number.service_calls[0].dialog)


def test_all_facts_passed_within_the_norm_gives_full_points() -> None:
    scenario = with_calls()
    result = evaluate_card_response(scenario, called(scenario, FULL), grammar=grammar_ok())
    component = result.components["service_call"]
    assert component.max == 13  # 15 of 115 with the six classic components
    assert component.score == component.max
    assert component.items[0]["facts_missing"] == []
    assert component.items[0]["within_norm"] is True
    assert result.total == 100
    assert "service_not_informed" not in {e.code for e in result.errors}


def test_forgotten_order_number_gives_a_partial_score() -> None:
    scenario = with_calls()
    result = evaluate_card_response(scenario, called(scenario, FULL[:3]), grammar=grammar_ok())
    component = result.components["service_call"]
    assert component.items[0]["facts_missing"] == ["order_number"]
    # 0.3 reached + 0.5 × 3/4 facts + 0.2 norm = 0.875
    assert component.score == round(component.max * 0.875, 1)


def test_over_the_norm_loses_the_time_share() -> None:
    scenario = with_calls()
    result = evaluate_card_response(scenario, called(scenario, FULL, seconds=200))
    component = result.components["service_call"]
    assert component.items[0]["within_norm"] is False
    assert component.score == round(component.max * 0.8, 1)


def test_no_call_gives_zero_and_the_detector() -> None:
    scenario = with_calls()
    attempt = perfect_card_attempt(scenario).model_copy(update={"service_calls": []})
    result = evaluate_card_response(scenario, attempt, grammar=grammar_ok())
    component = result.components["service_call"]
    assert component.score == 0
    assert component.items[0]["called"] is False
    errors = {e.code: e for e in result.errors}
    assert "service_not_informed" in errors
    assert "moek" in errors["service_not_informed"].explanation


def test_call_to_another_service_does_not_count() -> None:
    scenario = with_calls()
    result = evaluate_card_response(scenario, called(scenario, FULL, service="gkh"))
    assert result.components["service_call"].score == 0
    assert "service_not_informed" in {e.code for e in result.errors}


def test_unanswered_call_does_not_count() -> None:
    scenario = with_calls()
    result = evaluate_card_response(scenario, called(scenario, FULL, answered=False))
    assert result.components["service_call"].score == 0


def test_rejected_card_does_not_need_the_call() -> None:
    scenario = with_calls("card_2-1_dubl")
    result = evaluate_card_response(scenario, perfect_card_attempt(scenario), grammar=grammar_ok())
    assert "service_not_informed" not in {e.code for e in result.errors}


def test_scenario_without_reference_calls_is_unchanged() -> None:
    scenario = card_scenario()
    result = evaluate_card_response(scenario, perfect_card_attempt(scenario), grammar=grammar_ok())
    assert "service_call" not in result.components
    assert result.total == 100
    assert sum(c.max for c in result.components.values()) == 100


def test_both_extra_components_normalize_together() -> None:
    scenario = with_calls()
    body = scenario.model_dump()
    body["card"]["address"]["house"] = "12"
    body["injected_errors"] = [
        {"field": "address.house", "wrong_value": "12", "correct_value": "10"}
    ]
    both = CardResponseScenario.model_validate(body)
    result = evaluate_card_response(both, perfect_card_attempt(both))
    maxima = {k: c.max for k, c in result.components.items()}
    assert set(maxima) == {
        "decision",
        "time",
        "status_chain",
        "comments",
        "data_check",
        "service_call",
        "typical_errors",
        "grammar",
    }
    assert sum(maxima.values()) == 100
    assert maxima["data_check"] == 14 and maxima["service_call"] == 11  # 20 and 15 of 135


def test_second_call_can_fix_the_first() -> None:
    scenario = with_calls()
    first = called(scenario, ["Алло, это диспетчер, перезвоню."]).service_calls[0]
    second = called(scenario, FULL).service_calls[0]
    attempt = perfect_card_attempt(scenario).model_dump()
    attempt["service_calls"] = [
        first.model_dump(),
        {**second.model_dump(), "started_at": at(100), "ended_at": at(160)},
    ]
    result = evaluate_card_response(scenario, CardResponseAttempt.model_validate(attempt))
    assert result.components["service_call"].score == result.components["service_call"].max


def test_open_call_uses_the_last_turn_for_its_length() -> None:
    scenario = with_calls()
    attempt = called(scenario, FULL).model_dump()
    attempt["service_calls"][0]["ended_at"] = None
    result = evaluate_card_response(scenario, CardResponseAttempt.model_validate(attempt))
    item = result.components["service_call"].items[0]
    assert item["seconds"] is not None and item["within_norm"] is True
    assert timedelta(seconds=item["seconds"]) < timedelta(seconds=120)
