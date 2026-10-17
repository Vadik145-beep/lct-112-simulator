"""A complication on site (issue #263): on difficulty 3 the squad's specialist calls after the
arrival and asks for another service of the card; the dispatcher who does not call it gets
«Не оповещена служба об осложнении»."""

from __future__ import annotations

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
    errors = errors_of(hard(), complication_call(answered=False))
    assert "complication_not_passed" not in errors
    assert f"«{complication.TITLE}»" in errors["report_not_taken"]
