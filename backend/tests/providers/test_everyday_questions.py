"""The dispatcher's everyday questions on the phone (stand 19.10.2026, prerecorded replies:
«Нужна ли дополнительная помощь?» → «Это не ко мне», «Когда закончите?» → «Принято, работаем
дальше», «Сколько машин выезжает?» → «Принял, выезжаем» again, «Подъезд, этаж известны?» after
the squad left; free generation: the squad on its report said «Понял, направляю наряд»)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.dialog import officer
from app.domain.scenarios import officers, questions
from app.models import Attempt
from app.providers import dialog as dlg
from app.providers.dialog import CallerReply
from tests.providers.test_chief_order import AS_IN_CARD, CONFIRM, moek, squad
from tests.providers.test_officer_misheard import new_call

NOW = datetime(2026, 10, 19, 10, 0, tzinfo=UTC)
ADDRESS_FLOOR = CallerReply(
    text=officers.ADDRESS_AND_FLOOR, topics=["address"], operator_topics=["address"]
)


def say(scenario, attempt, call, text, provider: CallerReply = CONFIRM) -> str:
    """One phrase answered the way ``officer.say`` does it for prerecorded replies."""
    special = officer.special_reply(scenario, attempt, call, text)
    asked = officer.question_reply(scenario, attempt, call, text, NOW)
    reply = special[0] if special else asked or provider
    reply = officer.chief_reply(scenario, attempt, call, text, reply)
    call["dialog"] = [
        *call["dialog"],
        {"role": "operator", "text": text, "topics": reply.operator_topics},
        {"role": "caller", "text": reply.text, "topics": reply.topics},
    ]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return reply.text


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Вам нужна какая-то дополнительная помощь?", questions.INTENT_HELP),
        ("Помощь нужна?", questions.INTENT_HELP),
        ("Что-то ещё нужно от нас?", questions.INTENT_HELP),
        ("Как обстановка на месте?", questions.INTENT_SITUATION),
        ("Когда примерно закончите?", questions.INTENT_FINISH),
        ("Когда будете на месте?", questions.INTENT_ARRIVAL),
        ("Сколько машин выезжает?", questions.INTENT_FORCES),
        ("Претензии от жителей есть?", questions.INTENT_RESIDENTS),
        ("Спасибо, жду доклада.", questions.INTENT_THANKS),
        ("Принял.", questions.INTENT_THANKS),
        ("Улица Молостовых, дом 10.", None),
        ("Высылайте бригаду.", None),
    ],
)
def test_the_question_is_understood(text: str, intent: str | None) -> None:
    assert questions.intent_of(text) == intent


def test_the_chief_answers_questions_once_the_squad_is_out() -> None:
    scenario = moek(1)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    assert say(scenario, attempt, call, AS_IN_CARD) == f"Принял, выезжаем. Наряд {squad(scenario)}."
    on_way = officers.PROGRESS_REPLIES["response_started"]
    assert say(scenario, attempt, call, "Вам нужна дополнительная помощь?") == questions.NO_HELP
    assert say(scenario, attempt, call, "Сколько машин выезжает?") == questions.FORCES_ON_WAY
    assert say(scenario, attempt, call, "Когда будете на месте?") == on_way
    # The card's questions are over once he has left.
    assert say(scenario, attempt, call, "Адрес: Молостовых, 10.", ADDRESS_FLOOR) == on_way
    assert say(scenario, attempt, call, "Спасибо, жду доклада.") == questions.TAKEN
    # The number asked for is said, not swallowed.
    number = CallerReply(
        text=f"Наряд {squad(scenario)}, он и выезжает.", topics=["order_number"], operator_topics=[]
    )
    assert say(scenario, attempt, call, "Какой номер наряда?", number) == number.text


def test_no_bare_taken_before_the_chief_knows_what_happened() -> None:
    """Stand 19.10.2026: «Добрый день, это диспетчер ДДС.» → «Принял.», which leads nowhere."""
    scenario = moek(1)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    assert say(scenario, attempt, call, "Добрый день, это диспетчер ДДС.") == officer.ASK_INCIDENT
    repeat = CallerReply(
        text="Повторите, пожалуйста, плохо слышно.", topics=["repeat"], operator_topics=[]
    )
    assert say(scenario, attempt, call, "Алло, это диспетчер.", repeat) == officer.ASK_INCIDENT
    said = say(scenario, attempt, call, "Улица Молостовых, дом 10, корпус 1.", ADDRESS_FLOOR)
    assert said == officer.ASK_INCIDENT  # not «Подъезд, этаж известны?»


def report_call(status: str) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "service": "moek",
        "kind": officer.KIND_REPORT,
        "report_status": status,
        "answered": True,
        "dialog": [],
    }


@pytest.mark.parametrize(
    ("status", "text", "line"),
    [
        ("arrived", "Нужна ли дополнительная помощь?", questions.NO_HELP),
        ("arrived", "Когда примерно закончите?", questions.STILL_WORKING),
        ("works_started", "Как обстановка на месте?", questions.BY_PLAN),
        ("works_started", "Принял.", questions.GOING_ON),
        ("works_done", "Как обстановка?", questions.ALL_CALM),
        ("works_done", "Когда закончите?", questions.ALREADY_DONE),
        ("works_done", "Принял, спасибо.", questions.ROGER),
        ("works_done", "Претензии от жителей есть?", questions.NO_DANGER),
    ],
)
def test_the_squad_answers_by_its_report(status: str, text: str, line: str) -> None:
    call = report_call(status)
    attempt = Attempt(service_calls=[call])
    reply = officer.question_reply(moek(1), attempt, call, text, NOW)
    assert reply is not None and reply.text == line


def test_the_squad_reporting_gets_its_own_generate_prompt() -> None:
    scenario = moek(1)
    report = next(r for r in scenario.reference.reports if r.status == "arrived")
    leader = officers.report_scenario(scenario, report, "МОЭК")
    ctx = dlg.DialogContext(scenario=leader, history=[], conversation_id="t", role=dlg.ROLE_OFFICER)
    system = dlg.generate_messages(ctx, "Как обстановка?")[0]["content"]
    assert "НАЧАЛЬНИКА АВАРИЙНОЙ БРИГАДЫ" in system and report.text in system
    assert "бригада на месте" in system
    assert "направляю наряд" not in system.lower()
    chief = officer.call_scenario(scenario, new_call("moek"), Attempt(service_calls=[]))
    ctx = dlg.DialogContext(scenario=chief, history=[], conversation_id="t", role=dlg.ROLE_OFFICER)
    assert "направляю наряд" not in dlg.generate_messages(ctx, "Алло")[0]["content"].lower()


def test_a_real_answer_with_the_word_go_is_kept() -> None:
    """Free generation, stand 19.10.2026: «Когда будете на месте?» got a bare «Принял.»."""
    scenario = moek(1)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    say(scenario, attempt, call, AS_IN_CARD)
    soon = CallerReply(
        text="Будем минут через двадцать, уже выезжаем.", topics=["progress"], operator_topics=[]
    )
    assert officer.chief_reply(scenario, attempt, call, "Когда будете?", soon).text == soon.text
    again = CallerReply(
        text=f"Принял, выезжаем. Наряд {squad(scenario)}.", topics=["confirm"], operator_topics=[]
    )
    assert officer.chief_reply(scenario, attempt, call, "Ясно.", again).text == officer.TAKEN
