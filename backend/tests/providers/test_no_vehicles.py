"""No vehicles (issue #263): on a card of difficulty 3 whose turn is «машин нет» the squad
chief takes the address and has no vehicle; the squad stays until the head of the organization
finds one, and the dispatcher who never tells the head gets «Не доложено руководителю»."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.scenarios import misheard, no_vehicles, officers, staff
from app.models import Attempt
from app.providers.dialog import CallerReply
from app.training import reports
from tests.domain.evaluation.helpers import card_scenario, grammar_ok, perfect_card_attempt

NAME = "card_gkh_1_tech_v_podezde"  # улица Свободы, дом 42, корпус 2; own service gkh
ADDRESS = "Улица Свободы, дом 42, корпус 2, затопление в подъезде, пострадавших нет."
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
# What the dialog provider would say to an ordinary phrase.
PROVIDER = CallerReply(
    text="Принято. Как будем на месте, доложу.", topics=["confirm"], operator_topics=[]
)


def hard() -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": 3})


def chief_call(plan: str = no_vehicles.PLAN_NO_VEHICLES) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "service": "gkh",
        "kind": officer.KIND_OUTGOING,
        "started_at": NOW.isoformat(),
        "answered": True,
        "dialog": [],
        no_vehicles.PLAN_KEY: plan,
    }


def head_call(start: datetime) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "service": "gkh",
        "kind": officer.KIND_STAFF,
        no_vehicles.ROLE_KEY: staff.ROLE_HEAD,
        "started_at": start.isoformat(),
        "answered": True,
        "dialog": [],
    }


def talk(scenario: CardResponseScenario, attempt: Attempt, call: dict, text: str) -> str:
    """One phrase of the dispatcher answered as ``officer.say`` answers it."""
    special = officer.special_reply(scenario, attempt, call, text)
    reply, extra = special if special is not None else (PROVIDER, {})
    reply = officer.no_vehicles_reply(scenario, attempt, call, text, reply)
    call["dialog"] = [
        *call["dialog"],
        {"role": "operator", "text": text, "topics": reply.operator_topics},
        {"role": "caller", "text": reply.text, "topics": reply.topics, **extra},
    ]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return reply.text


def accepted(attempt: Attempt) -> None:
    attempt.status_log = [{"status": "accepted", "at": NOW.isoformat(), "by": "student"}]


def test_turns_alternate_in_the_order_cards_are_worked() -> None:
    assert no_vehicles.plan_after([]) == no_vehicles.PLAN_NO_VEHICLES
    assert no_vehicles.plan_after(["no_vehicles"]) == no_vehicles.PLAN_COMPLICATION
    assert no_vehicles.plan_after(["no_vehicles", "complication"]) == "no_vehicles"
    assert no_vehicles.plan_after(["complication", "no_vehicles"]) == "no_vehicles"


def test_no_vehicles_after_the_house_is_settled_then_the_head_finds_one() -> None:
    scenario = hard()
    call = chief_call()
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    # The house is misheard first; «машин нет» comes with the correction.
    assert talk(scenario, attempt, call, ADDRESS).startswith("Записал:")
    said = talk(scenario, attempt, call, "Нет, дом 42.")
    assert said == f"Понял, дом 42. Исправил, записал. {no_vehicles.SHORTAGE}"
    assert misheard.state(attempt.service_calls).fixed
    call["ended_at"] = (NOW + timedelta(seconds=40)).isoformat()

    # One truth: the squad has no vehicles and goes nowhere.
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_NO_VEHICLES
    assert reports.dispatched_at(scenario, attempt) is None
    assert "no_vehicles_not_reported" in errors_of(scenario, attempt)

    # Calling the chief again changes nothing.
    again = chief_call()
    attempt.service_calls = [*attempt.service_calls, again]
    assert talk(scenario, attempt, again, "Ну что, выезжаете?") == no_vehicles.STILL_NONE

    head = head_call(NOW + timedelta(seconds=60))
    attempt.service_calls = [*attempt.service_calls, head]
    assert talk(scenario, attempt, head, "Добрый день, у бригады нет свободных машин.") == (
        no_vehicles.RESOLVED
    )
    head["ended_at"] = (NOW + timedelta(seconds=90)).isoformat()
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_PENDING
    assert reports.dispatched_at(scenario, attempt) == NOW + timedelta(seconds=90)
    assert "no_vehicles_not_reported" not in errors_of(scenario, attempt)


def test_a_complication_turn_has_vehicles() -> None:
    scenario = hard()
    call = chief_call(no_vehicles.PLAN_COMPLICATION)
    attempt = Attempt(service_calls=[call])
    talk(scenario, attempt, call, ADDRESS)
    assert talk(scenario, attempt, call, "Да.") == misheard.AGREED
    assert not no_vehicles.announced(attempt.service_calls)


def test_the_head_finds_nothing_when_nothing_is_missing() -> None:
    scenario = hard()
    head = head_call(NOW)
    attempt = Attempt(service_calls=[head])
    assert officer.head_reply(attempt, head, "Нет свободных машин.") is None
    built = officer.call_scenario(scenario, head, attempt)
    assert built.caller.opening == "Руководитель ЕДЦ ЖКХ, слушаю."


def errors_of(scenario: CardResponseScenario, attempt: Attempt) -> dict[str, str]:
    data = perfect_card_attempt(scenario).model_dump()
    data["service_calls"] = [
        {
            "service": c["service"],
            "kind": c["kind"],
            "role": c.get(no_vehicles.ROLE_KEY),
            "started_at": NOW,
            "answered": True,
            "dialog": [
                {"role": t["role"], "text": t["text"], "topics": t["topics"]} for t in c["dialog"]
            ],
        }
        for c in attempt.service_calls
    ]
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(data), grammar=grammar_ok()
    )
    return {e.code: e.explanation for e in result.errors}
