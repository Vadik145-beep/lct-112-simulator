"""The squad chief no longer mishears the house (decision of 18.10.2026, issue #263): the bot
never says the address wrong on purpose, at any difficulty. The 112 operator's mistakes in the
card stay and are scored as before."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.scenarios import misheard
from app.models import Attempt
from tests.domain.evaluation.helpers import card_scenario, grammar_ok, perfect_card_attempt

NAME = "card_gkh_1_tech_v_podezde"  # улица Свободы, дом 42, корпус 2; own service gkh
ADDRESS = "Улица Свободы, дом 42, корпус 2, затопление в подъезде, пострадавших нет."
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


@pytest.fixture
def scenario() -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": 2})


def new_call(service: str = "gkh", **extra: object) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "service": service,
        "kind": officer.KIND_OUTGOING,
        "started_at": NOW.isoformat(),
        "answered": True,
        "dialog": [],
        **extra,
    }


def talk(scenario: CardResponseScenario, attempt: Attempt, call: dict, text: str) -> str | None:
    """One phrase of the dispatcher; the officer's scripted answer is stored like ``say``
    stores it. ``None`` when the dialog provider would answer instead."""
    special = officer.special_reply(scenario, attempt, call, text)
    turns = [{"role": "operator", "text": text, "topics": [], "at": NOW.isoformat()}]
    if special is not None:
        reply, extra = special
        turns[0]["topics"] = reply.operator_topics
        turns.append({"role": "caller", "text": reply.text, "topics": reply.topics, **extra})
    call["dialog"] = [*call["dialog"], *turns]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return special[0].text if special is not None else None


def test_wrong_house_is_two_off_and_keeps_the_letter() -> None:
    assert misheard.wrong_house("13") == "15"
    assert misheard.wrong_house("42А") == "44А"
    assert misheard.wrong_house("") is None
    assert misheard.wrong_house("б/н") is None


def test_difficulty_one_hears_right() -> None:
    easy = card_scenario(NAME)
    call = new_call()
    attempt = Attempt(service_calls=[call])
    assert talk(easy, attempt, call, ADDRESS) is None


def test_no_misheard_house_at_any_difficulty() -> None:
    for level in (2, 3):
        hard = card_scenario(NAME).model_copy(update={"difficulty": level})
        call = new_call()
        attempt = Attempt(service_calls=[call])
        said = talk(hard, attempt, call, ADDRESS)
        assert said is None or not said.startswith("Записал:")
        assert misheard.state(attempt.service_calls) is None
        assert "misheard_not_fixed" not in errors_of(hard, attempt)


def test_only_the_first_address_to_the_own_service(scenario: CardResponseScenario) -> None:
    other = new_call(service="moek")
    repeat = new_call(repeat=True)
    attempt = Attempt(service_calls=[other, repeat])
    assert talk(scenario, attempt, other, ADDRESS) is None
    assert talk(scenario, attempt, repeat, ADDRESS) is None
    assert talk(scenario, attempt, repeat, "Пострадавших нет.") is None


def errors_of(scenario: CardResponseScenario, attempt: Attempt) -> dict[str, str]:
    """The review of a perfect card whose calls are the ones of ``attempt``."""
    logs = perfect_card_attempt(scenario).model_dump()
    logs["service_calls"] = [
        {
            "service": c["service"],
            "kind": c["kind"],
            "report_status": c.get("report_status"),
            "started_at": NOW,
            "answered": True,
            "dialog": [
                {"role": t["role"], "text": t["text"], "topics": t["topics"]} for t in c["dialog"]
            ],
        }
        for c in attempt.service_calls
    ]
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(logs), grammar=grammar_ok()
    )
    return {e.code: e.explanation for e in result.errors}
