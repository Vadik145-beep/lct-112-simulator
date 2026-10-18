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
from app.training.present import squad_state_told
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
    reply = officer.chief_reply(scenario, attempt, call, text, reply)
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
    # «Машин нет» once the chief knows where and what; no misheard house (18.10.2026).
    assert talk(scenario, attempt, call, ADDRESS) == no_vehicles.ANNOUNCE
    assert misheard.state(attempt.service_calls) is None
    assert talk(scenario, attempt, call, "Выезжайте.") == no_vehicles.STILL_NONE
    call["ended_at"] = (NOW + timedelta(seconds=40)).isoformat()

    # One truth: the squad has no vehicles and goes nowhere.
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_NO_VEHICLES
    assert reports.dispatched_at(scenario, attempt) is None
    assert "no_vehicles_not_reported" in errors_of(scenario, attempt)

    # Calling the chief again changes nothing.
    again = chief_call()
    attempt.service_calls = [*attempt.service_calls, again]
    assert talk(scenario, attempt, again, "Ну что, выезжаете?") == no_vehicles.STILL_NONE

    # Told of the shortage without the address, the head asks it first (18.10.2026).
    head = head_call(NOW + timedelta(seconds=60))
    attempt.service_calls = [*attempt.service_calls, head]
    assert talk(scenario, attempt, head, "Добрый день, у бригады нет свободных машин.") == (
        no_vehicles.ASK_ADDRESS
    )
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_NO_VEHICLES
    assert talk(scenario, attempt, head, "Улица Свободы, дом 42, корпус 2.") == no_vehicles.RESOLVED
    head["ended_at"] = (NOW + timedelta(seconds=90)).isoformat()
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_PENDING
    assert reports.dispatched_at(scenario, attempt) == NOW + timedelta(seconds=90)
    assert "no_vehicles_not_reported" not in errors_of(scenario, attempt)


def test_the_phone_says_no_vehicles_only_once_the_chief_has_said_it() -> None:
    scenario = hard()
    call = chief_call()
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    # Dialled and hung up at once: the squad is held, the trainee has been told nothing.
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_NO_VEHICLES
    assert squad_state_told(attempt) == officers.SQUAD_STATE_WAITING
    talk(scenario, attempt, call, ADDRESS)
    assert squad_state_told(attempt) == officers.SQUAD_STATE_NO_VEHICLES


def test_a_complication_turn_has_vehicles() -> None:
    scenario = hard()
    call = chief_call(no_vehicles.PLAN_COMPLICATION)
    attempt = Attempt(service_calls=[call])
    said = talk(scenario, attempt, call, ADDRESS)
    assert said.startswith("Принял, выезжаем. Наряд")
    assert not no_vehicles.announced(attempt.service_calls)


def test_the_head_finds_nothing_when_nothing_is_missing() -> None:
    scenario = hard()
    head = head_call(NOW)
    attempt = Attempt(service_calls=[head])
    assert officer.head_reply(scenario, attempt, head, "Нет свободных машин.") is None
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


def test_the_head_named_the_address_at_once_finds_a_vehicle_at_once() -> None:
    scenario = hard()
    call = chief_call()
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    talk(scenario, attempt, call, ADDRESS)
    head = head_call(NOW + timedelta(seconds=60))
    attempt.service_calls = [*attempt.service_calls, head]
    said = talk(scenario, attempt, head, "Улица Свободы, дом 42, корпус 2, у бригады нет машин.")
    assert said == no_vehicles.RESOLVED


def test_after_the_heads_vehicle_a_call_to_the_chief_is_a_repeat() -> None:
    """Stand 18.10.2026: the squad left on the head's vehicle and a call to its chief started
    as a new one, «Короче, что у вас?» and «Передано: пока ничего»."""
    scenario = hard()
    call = chief_call()
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    talk(scenario, attempt, call, ADDRESS)
    call["facts_passed"] = ["address", "incident_type", "injured"]
    call["ended_at"] = (NOW + timedelta(seconds=40)).isoformat()
    assert not officer.squad_out(attempt, scenario)
    head = head_call(NOW + timedelta(seconds=60))
    attempt.service_calls = [*attempt.service_calls, head]
    talk(scenario, attempt, head, "Улица Свободы, дом 42, корпус 2: у бригады нет свободных машин.")
    head["ended_at"] = (NOW + timedelta(seconds=90)).isoformat()
    assert officer.squad_out(attempt, scenario)


def test_reports_say_the_real_house_not_the_operators_mistake() -> None:
    """Stand 18.10.2026: «прибыли … дом 23» while the caller and the dispatcher said 24."""
    body = card_scenario(NAME).model_dump()
    body["card"]["address"]["house"] = "41"  # the 112 operator's mistake; the caller said 42
    body["injected_errors"] = [
        {"field": "address.house", "wrong_value": "41", "correct_value": "42", "hint_level": 2}
    ]
    scenario = CardResponseScenario.model_validate(body)
    report = next(r for r in scenario.reference.reports if r.status == "arrived").model_copy(
        update={"text": "Прибыли по адресу: улица Свободы, дом 41, корпус 2."}
    )
    said = officer.report_said(scenario, Attempt(service_calls=[]), report)
    assert "дом 42" in said and "дом 41" not in said


def test_the_chief_knows_his_specialist_asked_for_another_service() -> None:
    """Stand 18.10.2026: told «мы запросили подкрепление», the chief said «запрос передан»."""
    scenario = hard()
    report = {
        "id": "r-c",
        "service": "gkh",
        "kind": officer.KIND_REPORT,
        "report_status": "complication",
        "started_at": NOW.isoformat(),
        "answered": True,
        "ended_at": NOW.isoformat(),
        "dialog": [],
    }
    attempt = Attempt(service_calls=[report])
    built = officer.officer_scenario(scenario, "gkh", "ЕДЦ ЖКХ", attempt, repeat=True)
    assert "попросил диспетчера вызвать" in built.caller.facts["осложнение"]
