"""Reaching the officer is not reporting to them (issue #127): a call in which the dispatcher
passed nothing scores nothing, is named as its own mistake, and dispatches no squad."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.evaluation.detectors import CARD_DETECTORS, CardContext
from app.domain.evaluation.schemas import CardResponseAttempt
from app.domain.evaluation.service_call import service_call_component
from tests.domain.evaluation.helpers import card_scenario

SCENARIO = card_scenario("card_gkh_1_tech_v_podezde")  # звонок в ГКХ, четыре обязательных факта
MAX_POINTS = 20
T0 = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
REPORTED = "улица Свободы, дом 42, течёт стояк холодной воды, пострадавших нет, код подъезда 5В"


def at(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat()


def call(*said: str, ended: float = 30) -> dict:
    dialog = [{"role": "caller", "text": "Дежурный слушает.", "topics": ["greeting"], "at": at(15)}]
    for i, text in enumerate(said):
        dialog.append({"role": "operator", "text": text, "topics": [], "at": at(16 + i)})
    return {
        "service": "gkh",
        "kind": "outgoing",
        "started_at": at(15),
        "answered": True,
        "ended_at": at(ended),
        "dialog": dialog,
    }


def attempt(*calls: dict) -> CardResponseAttempt:
    return CardResponseAttempt.model_validate(
        {
            "issued_at": at(0),
            "status_log": [{"status": "accepted", "at": at(10)}],
            "flagged_fields": [],
            "service_calls": list(calls),
        }
    )


def score(*calls: dict) -> float:
    return service_call_component(SCENARIO, attempt(*calls), MAX_POINTS).score


def errors(*calls: dict) -> set[str]:
    a = attempt(*calls)
    ctx = CardContext(SCENARIO, a, a.status_log)
    return {code for code, detect in CARD_DETECTORS.items() if detect(ctx) is not None}


# --- the score -------------------------------------------------------------------------------


def test_a_call_that_passed_nothing_scores_as_if_it_was_never_made() -> None:
    assert score(call(ended=17)) == score() == 0.0


def test_a_greeting_is_not_a_report() -> None:
    assert score(call("Алло, добрый день.", ended=18)) == 0.0


def test_hanging_up_at_once_is_not_a_fast_call() -> None:
    """The norm share used to be given for any call short enough, and a call of two seconds
    always is."""
    assert score(call(ended=16)) == 0.0


def test_a_call_that_passed_one_fact_still_counts() -> None:
    partial = score(call("улица Свободы, дом 42"))
    assert 0.0 < partial < MAX_POINTS


def test_a_full_report_scores_in_full() -> None:
    assert score(call(REPORTED)) == MAX_POINTS


def test_the_better_of_two_calls_counts() -> None:
    """A silent call followed by a proper one is not punished for the first."""
    assert score(call(ended=17), call(REPORTED)) == MAX_POINTS


# --- the mistake it is named ------------------------------------------------------------------


def test_a_silent_call_is_named_as_its_own_mistake() -> None:
    found = errors(call(ended=17))
    assert "service_call_silent" in found
    assert "service_not_informed" not in found  # the dispatcher did call, so that one would lie


def test_not_calling_at_all_is_still_the_other_mistake() -> None:
    found = errors()
    assert "service_not_informed" in found
    assert "service_call_silent" not in found


def test_a_proper_report_raises_neither() -> None:
    found = errors(call(REPORTED))
    assert "service_call_silent" not in found
    assert "service_not_informed" not in found
