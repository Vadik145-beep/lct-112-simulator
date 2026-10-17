"""The squad chief mishears the house (issue #263): on difficulty 2 he repeats the address
with a wrong house, the dispatcher corrects him or lets it pass; a squad sent to the wrong
house names it in its reports, and the review counts the miss."""

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


def test_corrected_in_the_same_call_is_no_error(scenario: CardResponseScenario) -> None:
    call = new_call()
    attempt = Attempt(service_calls=[call])
    assert talk(scenario, attempt, call, ADDRESS) == "Записал: улица Свободы, дом 44, корпус 2?"
    assert talk(scenario, attempt, call, "Нет.") == misheard.ASK_HOUSE
    assert talk(scenario, attempt, call, "Дом сорок два.") == ("Понял, дом 42. Исправил, записал.")
    found = misheard.state(attempt.service_calls)
    assert found is not None and found.fixed and found.house_said == "44"
    # Asked once per card: the house named again later is just a fact.
    assert talk(scenario, attempt, call, ADDRESS) is None
    assert "misheard_not_fixed" not in errors_of(scenario, attempt)


def test_let_pass_sends_the_squad_to_the_wrong_house(scenario: CardResponseScenario) -> None:
    call = new_call()
    attempt = Attempt(service_calls=[call])
    talk(scenario, attempt, call, ADDRESS)
    assert talk(scenario, attempt, call, "Да.") == misheard.AGREED
    found = misheard.state(attempt.service_calls)
    assert found is not None and found.squad_has_wrong_house

    report = scenario.reference.reports[0]
    text = misheard.report_text(report.text, scenario.card, found)
    assert "улица Свободы, дом 44, корпус 2" in text
    assert "дом 42" not in text
    errors = errors_of(scenario, attempt)
    assert "дом 44" in errors["misheard_not_fixed"]
    assert "выехала по неверному адресу" in errors["misheard_not_fixed"]

    # Corrected on the squad's report: the squad turns round, the error stays.
    report_call = new_call(kind=officer.KIND_REPORT, report_status=report.status)
    attempt.service_calls = [*attempt.service_calls, report_call]
    said = talk(scenario, attempt, report_call, "Не тот дом, нужен дом 42.")
    assert said == "Понял, дом 42. Ошиблись адресом, переезжаем."
    found = misheard.state(attempt.service_calls)
    assert found is not None and found.fixed_late and not found.squad_has_wrong_house
    assert misheard.report_text(report.text, scenario.card, found) == report.text
    assert "исправлен позже" in errors_of(scenario, attempt)["misheard_not_fixed"]


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
