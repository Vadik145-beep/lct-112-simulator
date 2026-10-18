"""A complication on site (issue #263): on difficulty 3 the squad's specialist calls after the
arrival and asks for another service of the card; the dispatcher who does not call it gets
«Не оповещена служба об осложнении»."""

from __future__ import annotations

from datetime import timedelta

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.scenarios import complication
from app.models import Attempt
from tests.domain.evaluation.helpers import at, card_scenario, grammar_ok, perfect_card_attempt

NAME = "card_gkh_1_tech_v_podezde"  # own service gkh, the card also names territorial_oiv


def hard() -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": 3})


def test_specialist_calls_after_the_arrival_on_difficulty_three() -> None:
    reports = complication.timeline(hard())
    assert [r.status for r in reports] == [
        "response_started",
        "arrived",
        complication.STATUS,
        "works_started",
        "works_done",
    ]
    assert reports[2].text == (
        "Алло, диспетчер? Специалист аварийной бригады, мы на месте. Нужно перекрыть двор, "
        "жильцы не дают работать. Нужна помощь управы, сообщите им."
    )
    medium = card_scenario(NAME).model_copy(update={"difficulty": 2})
    assert complication.STATUS not in [r.status for r in complication.timeline(medium)]


def test_no_complication_without_another_service_on_the_card() -> None:
    scenario = hard()
    own_only = [n for n in scenario.card.notified if n.service == scenario.service]
    card = scenario.card.model_copy(update={"notified": own_only})
    alone = scenario.model_copy(update={"card": card})
    assert not complication.applies(alone)
    assert complication.STATUS not in [r.status for r in complication.timeline(alone)]


def test_the_specialist_speaks_in_his_own_voice() -> None:
    scenario = hard()
    call = {"service": "gkh", "kind": "report", "report_status": complication.STATUS}
    call["opening"] = complication.report(scenario).text
    built = officer.call_scenario(scenario, call, Attempt(service_calls=[]))
    assert built.caller.voice == complication.SPECIALIST_VOICE
    assert built.caller.opening.startswith("Алло, диспетчер? Специалист")
    assert complication.OFF_THE_POINT in [r.text for r in built.replies]


def complication_call(*, answered: bool = True) -> dict:
    return {
        "service": "gkh",
        "kind": "report",
        "report_status": complication.STATUS,
        "started_at": at(400),
        "answered": answered,
        "ended_at": at(420),
        "end_reason": None if answered else "not_taken",
        "dialog": [
            {"role": "caller", "text": complication.report(hard()).text, "topics": ["report"]},
            {"role": "operator", "text": "Понял, сообщу.", "topics": []},
        ]
        if answered
        else [],
    }


def call_to(service: str, start: int) -> dict:
    return {
        "service": service,
        "kind": "outgoing",
        "started_at": at(start),
        "answered": True,
        "ended_at": at(start + 30),
        "dialog": [
            {"role": "caller", "text": "Дежурный управы, слушаю.", "topics": ["greeting"]},
            {"role": "operator", "text": "Улица Свободы, 42, нужно перекрыть двор.", "topics": []},
        ],
    }


def errors_of(scenario: CardResponseScenario, *extra: dict) -> dict[str, str]:
    data = perfect_card_attempt(scenario).model_dump()
    data["service_calls"] = [*data["service_calls"], *extra]
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(data), grammar=grammar_ok()
    )
    return {e.code: e.explanation for e in result.errors}


def test_complication_not_passed_until_the_service_is_called() -> None:
    scenario = hard()
    errors = errors_of(scenario, complication_call())
    assert "помощи управы" in errors["complication_not_passed"]
    # The complication is a call, not a status: the report checks do not ask for it.
    assert "report_not_reflected" not in errors
    assert "complication_not_passed" not in errors_of(
        scenario, complication_call(), call_to("territorial_oiv", 430)
    )
    # A call to the service made before the complication does not tell it about it.
    assert "complication_not_passed" in errors_of(
        scenario, call_to("territorial_oiv", 100), complication_call()
    )


def test_unanswered_complication_is_a_report_not_taken() -> None:
    scenario = hard()
    data = perfect_card_attempt(scenario).model_dump()
    # Nothing after the missed call told the dispatcher: not «не оповещена служба».
    data["service_calls"] = [c for c in data["service_calls"] if c["started_at"] < at(400)] + [
        complication_call(answered=False)
    ]
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(data), grammar=grammar_ok()
    )
    errors = {e.code: e.explanation for e in result.errors}
    assert "complication_not_passed" not in errors
    assert f"«{complication.TITLE}»" in errors["report_not_taken"]


def chief_again(start: int, *, answered: bool = True) -> dict:
    return {
        "id": f"chief-{start}",
        "service": "gkh",
        "kind": "outgoing",
        "repeat": True,
        "started_at": at(start),
        "answered": answered,
        "ended_at": at(start + 20),
        "dialog": [],
    }


def test_the_squad_tells_the_complication_the_dispatcher_did_not_take() -> None:
    """Stand 18.10.2026: the specialist's call dropped, then the chief said «осматриваемся»
    and the report answered «помощь не требуется»."""
    scenario = hard()
    missed = {**complication_call(answered=False), "id": "spec"}
    attempt = Attempt(service_calls=[missed])
    line = complication.unheard_line(scenario)
    assert "управы" in line

    chief = chief_again(450, answered=False)
    attempt.service_calls = [missed, chief]
    built = officer.call_scenario(scenario, chief, attempt)
    assert built.caller.opening.endswith(line)
    assert "не дозвонился" in built.caller.facts["осложнение"]

    specialist = {"id": "s", "service": "gkh", "kind": "staff", "role": "squad_specialist"}
    built = officer.call_scenario(scenario, specialist, attempt)
    assert complication.specialist_line(scenario) in built.caller.opening

    report = {"id": "w", "service": "gkh", "kind": "report", "report_status": "works_started"}
    built = officer.call_scenario(scenario, report, attempt)
    assert built.caller.opening.endswith(line)
    # Told once in an answered call: the next calls know it but do not open with it again.
    attempt.service_calls = [missed, {**chief, "answered": True}]
    built = officer.call_scenario(scenario, report, attempt)
    assert line not in built.caller.opening
    assert "осложнение" in built.caller.facts
    # Told by the chief, not called: «не оповещена служба».
    plain = {k: v for k, v in chief.items() if k not in ("id", "repeat")}
    told = errors_of(scenario, complication_call(answered=False), {**plain, "answered": True})
    assert "complication_not_passed" in told


def test_the_complication_heard_is_not_told_again() -> None:
    scenario = hard()
    heard = {**complication_call(), "id": "spec"}
    chief = chief_again(450)
    attempt = Attempt(service_calls=[heard, chief])
    built = officer.call_scenario(scenario, chief, attempt)
    assert complication.unheard_line(scenario) not in built.caller.opening
    assert "попросил диспетчера" in built.caller.facts["осложнение"]


def test_the_specialist_rings_once_more_when_not_taken() -> None:
    from app.training import reports

    scenario = hard()
    attempt = perfect_card_attempt(scenario)
    rows = [c.model_dump(mode="json") for c in attempt.service_calls]
    arrived = [c for c in rows if c.get("report_status") in ("response_started", "arrived")]
    missed = {**complication_call(answered=False), "started_at": at(400).isoformat()}
    missed["ended_at"] = at(420).isoformat()
    model = Attempt(
        service_calls=[*arrived, missed],
        status_log=[s.model_dump(mode="json") for s in attempt.status_log],
        response_status="arrived",
    )
    later = at(420) + timedelta(seconds=complication.AFTER_SECONDS + 1)
    due = reports.next_report(scenario, model, later)
    assert due is not None and due.status == complication.STATUS
    model.service_calls = [*model.service_calls, missed]
    due = reports.next_report(scenario, model, later + timedelta(minutes=5))
    assert due is not None and due.status == "works_started"
