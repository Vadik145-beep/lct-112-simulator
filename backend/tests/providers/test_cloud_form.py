"""The cloud's form after a call to the squad chief (#263, decision of 17.10.2026): the
dispatcher's words are read by meaning, and the evaluation scores by the form instead of the
keyword table, which missed live speech."""

from __future__ import annotations

from datetime import UTC, datetime

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, ServiceCallLog
from app.domain.evaluation.service_call import call_facts, facts_from_analysis
from app.domain.scenarios import staff
from app.models import Attempt
from app.telephony import vapi
from tests.domain.evaluation.helpers import card_scenario, grammar_ok, perfect_card_attempt
from tests.providers.test_officer_misheard import new_call

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
TRAP = "card_err_2_moek_dom"  # the field says дом 19, the caller says 17
FULL = {
    "address_named": True,
    "house_named": "17",
    "incident_named": True,
    "injured_named": True,
    "access_named": False,
    "order_given": True,
    "house_corrected": False,
}


def chief_log(analysis: dict | None, said: str = "Дед с собакой бегает, туда езжайте.") -> dict:
    return {
        "service": "moek",
        "kind": "outgoing",
        "started_at": NOW,
        "answered": True,
        "ended_at": NOW,
        "dialog": [{"role": "operator", "text": said, "topics": []}],
        "analysis": analysis,
    }


def errors_of(scenario, *calls: dict) -> dict[str, str]:
    data = perfect_card_attempt(scenario).model_dump()
    data["service_calls"] = list(calls)
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(data), grammar=grammar_ok()
    )
    return {e.code: e.explanation for e in result.errors}


def test_facts_by_meaning_and_the_true_house() -> None:
    scenario = card_scenario(TRAP)
    assert facts_from_analysis(scenario, FULL) == ["address", "incident_type", "injured"]
    # The house of the operator's mistake is no address passed.
    assert "address" not in facts_from_analysis(scenario, {**FULL, "house_named": "19"})


def test_the_form_wins_over_the_keywords() -> None:
    scenario = card_scenario(TRAP)
    log = ServiceCallLog.model_validate(chief_log(FULL))
    assert call_facts(scenario, log) == ["address", "incident_type", "injured"]
    without = ServiceCallLog.model_validate(chief_log(None))
    assert "address" not in call_facts(scenario, without)


def test_order_and_wrong_house_read_from_the_form() -> None:
    scenario = card_scenario(TRAP)
    errors = errors_of(scenario, chief_log(FULL))
    assert "no_order_given" not in errors
    assert "wrong_address_passed" not in errors
    wrong = errors_of(
        scenario,
        chief_log({**FULL, "house_named": "19", "order_given": False}, said="Там дед с собакой."),
    )
    assert "no_order_given" in wrong
    assert "дом 17" in wrong["wrong_address_passed"]


def test_the_misheard_house_fixed_by_the_forms_ear() -> None:
    scenario = card_scenario(TRAP).model_copy(update={"difficulty": 2})
    misheard_turn = {"role": "caller", "text": "Записал: …, дом 19?", "topics": ["misheard"]}
    call = chief_log({**FULL, "house_corrected": True}, said="Семнадцатый, говорю.")
    call["dialog"] = [
        *call["dialog"],
        misheard_turn,
        {"role": "operator", "text": "ага", "topics": []},
    ]
    assert "misheard_not_fixed" not in errors_of(scenario, call)
    call["analysis"] = {**FULL, "house_corrected": False}
    assert "misheard_not_fixed" in errors_of(scenario, call)


def test_only_the_squad_chief_fills_the_form() -> None:
    scenario = card_scenario(TRAP)
    chief = officer.call_scenario(scenario, new_call("moek"), Attempt(service_calls=[]))
    plan = vapi.build_assistant(chief)["analysisPlan"]["structuredDataPlan"]
    assert plan["enabled"] is True and plan["schema"] == vapi.CHIEF_FORM
    duty = officer.call_scenario(scenario, new_call("gkh"), Attempt(service_calls=[]))
    assert "analysisPlan" not in vapi.build_assistant(duty)
    head_call = {**new_call("moek"), "kind": officer.KIND_STAFF, "role": staff.ROLE_HEAD}
    head = officer.call_scenario(scenario, head_call, Attempt(service_calls=[]))
    assert "analysisPlan" not in vapi.build_assistant(head)


def test_the_form_is_kept_on_the_call_with_its_facts() -> None:
    scenario = card_scenario(TRAP)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    officer.set_analysis(attempt, call["id"], FULL, scenario)
    record = officer.find_call(attempt, call["id"])
    assert record["analysis"] == FULL
    assert record["facts_passed"] == ["address", "incident_type", "injured"]
