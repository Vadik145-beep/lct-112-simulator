"""The cloud talks on its own, the record decides (#263, decision of 18.10.2026): the server says
nothing to Vapi in the middle of a call — no notes, no «say», no Live Call Control. «Машин нет»,
the hurry and the head's vehicle come through the prompts of the roles; after the call the
record of the cloud's own words decides about the squad: the chief named it, or the head took
a vehicle off a planned job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.dialog import officer
from app.domain.scenarios import no_vehicles, officers, staff
from app.models import Attempt
from app.telephony import cloud, vapi
from app.training import reports
from tests.domain.evaluation.helpers import card_scenario
from tests.providers.test_officer_misheard import new_call

NAME = "card_moek_1_net_otopleniya"  # улица Молостовых, 10, к. 1
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


def cloud_said(scenario, attempt: Attempt, call: dict, text: str) -> list[str]:
    """A phrase of the cloud as the record keeps it (``external_turn`` marks it)."""
    topics = officer.cloud_marks(scenario, attempt, call, text)
    call["dialog"] = [*call["dialog"], {"role": "caller", "text": text, "topics": topics}]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return topics


def accepted(attempt: Attempt) -> None:
    attempt.status_log = [{"status": "accepted", "at": NOW.isoformat(), "by": "student"}]


def test_the_server_never_takes_control_of_the_call() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    chief = officer.call_scenario(scenario, new_call("moek"), Attempt(service_calls=[]))
    assistant = vapi.build_assistant(chief)
    assert "monitorPlan" not in assistant
    prompt = vapi.role_prompt(chief)
    assert "Ты — начальник аварийной бригады" in prompt
    assert "Служебная подсказка" not in prompt
    assert "Так, стоп" in prompt  # the hurry is a line of the prompt
    for name in ("hint_message", "say_message", "said_note", "send_control", "control_url"):
        assert not hasattr(vapi, name)


def test_the_chief_who_named_his_squad_sends_it_after_the_call() -> None:
    scenario = card_scenario(NAME)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    assert cloud_said(scenario, attempt, call, "Пострадавшие есть?") == []
    number = officer.squad_order(scenario)
    words = " ".join(["восемь", "пять", "один", "три"]) if number == "8513" else number
    assert officer.TOPIC_ORDER_TAKEN in cloud_said(
        scenario, attempt, call, f"Принял, выезжаем. Наряд {words}."
    )
    assert reports.dispatched_at(scenario, attempt, cloud=True) is None  # the call goes on
    call["ended_at"] = (NOW + timedelta(seconds=50)).isoformat()
    assert reports.dispatched_at(scenario, attempt, cloud=True) == NOW + timedelta(seconds=50)


def test_a_chief_who_named_nobody_sends_nobody() -> None:
    scenario = card_scenario(NAME)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    accepted(attempt)
    cloud_said(scenario, attempt, call, "Принял, разберёмся.")
    call["ended_at"] = (NOW + timedelta(seconds=50)).isoformat()
    assert reports.dispatched_at(scenario, attempt, cloud=True) is None


def test_no_vehicles_from_the_prompts_and_the_head_from_the_record() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    chief_call = {**new_call("moek"), no_vehicles.PLAN_KEY: no_vehicles.PLAN_NO_VEHICLES}
    attempt = Attempt(service_calls=[chief_call])
    accepted(attempt)
    chief = officer.call_scenario(scenario, chief_call, attempt)
    assert chief.caller.facts[no_vehicles.FACT_KEY] == (
        "свободных машин нет, все на выездах, наряд не называешь; только если диспетчер прямо "
        "спросит, что делать, — «Это к руководителю, только он может снять машину.»"
    )
    assert "твой наряд" not in chief.caller.facts
    number = officer.squad_order(scenario)
    # The squad stays whatever the cloud says, the number included.
    assert cloud_said(scenario, attempt, chief_call, "Машин нет, все на выездах.") == [
        no_vehicles.TOPIC_NO_VEHICLES
    ]
    assert cloud_said(scenario, attempt, chief_call, f"Наряд {number}.") == []
    chief_call["ended_at"] = (NOW + timedelta(seconds=40)).isoformat()
    assert reports.dispatched_at(scenario, attempt, cloud=True) is None

    head_call = {**new_call("moek"), "kind": officer.KIND_STAFF, "role": staff.ROLE_HEAD}
    attempt.service_calls = [*attempt.service_calls, head_call]
    head = officer.call_scenario(scenario, head_call, attempt)
    assert head.caller.facts[no_vehicles.HEAD_FACT_KEY] == (
        "если адрес не назван — спроси адрес, потом снимаешь машину с планового выезда"
    )
    assert cloud_said(scenario, attempt, head_call, "По какому адресу?") == []
    assert cloud_said(scenario, attempt, head_call, "Снимаю машину с планового выезда.") == [
        no_vehicles.TOPIC_VEHICLES_FOUND
    ]
    head_call["ended_at"] = (NOW + timedelta(seconds=90)).isoformat()
    assert reports.dispatched_at(scenario, attempt, cloud=True) == NOW + timedelta(seconds=90)


def test_the_chief_knows_his_truth_from_the_prompt() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 2})
    chief_call = {**new_call("moek"), no_vehicles.PLAN_KEY: no_vehicles.PLAN_NO_VEHICLES}
    attempt = Attempt(service_calls=[chief_call])
    chief = officer.call_scenario(scenario, chief_call, attempt)
    prompt = vapi.role_prompt(chief)
    assert "свободных машин нет" in prompt
    # No misheard house (18.10.2026): the chief repeats the address as it was said.
    assert "Адрес вслух повторяешь один раз" in prompt
    easy = officer.call_scenario(card_scenario(NAME), new_call("moek"), Attempt(service_calls=[]))
    assert no_vehicles.FACT_KEY not in easy.caller.facts


def test_the_cloud_agrees_with_one_line() -> None:
    """«На согласие диспетчера отвечаешь «Принято».» — no list of words (18.10.2026)."""
    for prompt in (
        vapi.OFFICER_PROMPT,
        vapi.OFFICER_REPEAT_PROMPT,
        vapi.CHIEF_PROMPT,
        vapi.SQUAD_PROMPT,
    ):
        assert "не отвечаешь ничего" not in prompt.lower()
        assert "На согласие диспетчера отвечаешь «Принято»." in prompt
        assert "На «хорошо»" not in prompt


def test_the_head_does_not_know_the_squad_number() -> None:
    """Stand 18.10.2026: asked «номер бригады какой?», the head made up «Бригада No4»."""
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = new_call("moek", kind=officer.KIND_STAFF, **{no_vehicles.ROLE_KEY: staff.ROLE_HEAD})
    head = officer.call_scenario(scenario, call, Attempt(service_calls=[call]))
    prompt = vapi.role_prompt(head)
    assert "Номер вам начальник бригады скажет, он перезвонит" in prompt
    assert "твой наряд" not in head.caller.facts and "наряд бригады" not in head.caller.facts


def test_every_squad_number_is_said_digit_by_digit() -> None:
    """Stand 18.10.2026: the specialist said «Номер наряда: 4-641»."""
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    order = officer.squad_order(scenario)
    spoken = officers.spoken_digits(order)
    assert spoken.count(", ") == len(order) - 1
    chief = officer.call_scenario(scenario, new_call("moek"), Attempt(service_calls=[]))
    assert f"«{spoken}»" in chief.caller.facts["твой наряд"]
    report = next(r for r in scenario.reference.reports if r.status == "arrived")
    leader = officers.report_scenario(scenario, report, "МОЭК")
    assert f"«{spoken}»" in leader.caller.facts["наряд"]
    for built in (chief, leader):
        assert "по одной цифре через запятую" in " ".join(vapi.role_prompt(built).split())
    for prompt in (vapi.OFFICER_PROMPT, vapi.OFFICER_REPEAT_PROMPT, vapi.STAFF_PROMPT):
        assert "по одной цифре через запятую" in " ".join(prompt.split())


def test_the_journal_shows_dashes_not_heard_colons() -> None:
    """Stand 18.10.2026: Vapi's transcript of the bot came with colons for pauses."""
    assert cloud.written("Как начнём работы: доложу.") == "Как начнём работы — доложу."
    assert cloud.written("Едем на адрес:.") == "Едем на адрес."
    assert cloud.written("Номер наряда: 4-641.") == "Номер наряда — 4-641."
    assert cloud.written("Фиксирую адрес: Елисеевский переулок.") == (
        "Фиксирую адрес: Елисеевский переулок."
    )
    assert cloud.written("Принял, выезжаем.") == "Принял, выезжаем."


def test_the_chief_prompt_keeps_the_lines_that_held_it_before_the_final() -> None:
    """Stand 18.10.2026: questions in a heap, «Пострадавший. Есть.», «Принято» to a question."""
    for prompt in (
        vapi.CHIEF_PROMPT,
        vapi.OFFICER_PROMPT,
        vapi.OFFICER_REPEAT_PROMPT,
        vapi.SQUAD_PROMPT,
        vapi.STAFF_PROMPT,
    ):
        assert "#### Роль" in prompt and "#### Алгоритм диалога" in prompt
        assert "«Принято» на вопрос не отвечают" in " ".join(prompt.split())
    assert "больше одного вопроса за реплику" in vapi.CHIEF_PROMPT
    assert "Сам на свои вопросы не отвечаешь" in vapi.CHIEF_PROMPT
    assert "не торопясь" not in vapi.DIGITS_RULE


def test_the_no_vehicles_chief_has_nothing_about_going_out() -> None:
    """Stand 18.10.2026: on a «машин нет» card the chief said «Принял, выезжаем, наряд 7-4-3-1»."""
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = {**new_call("moek"), no_vehicles.PLAN_KEY: no_vehicles.PLAN_NO_VEHICLES}
    built = officer.call_scenario(scenario, call, Attempt(service_calls=[call]))
    prompt = " ".join(vapi.role_prompt(built).split())
    assert "Свободных машин сейчас нет" in prompt
    assert "«Принял, выезжаем" not in prompt
    assert "пять, два, семь, два" not in prompt
    assert "твой наряд" not in prompt


def test_not_ours_is_said_at_once() -> None:
    assert "Адрес не повторяешь" in " ".join(vapi.CHIEF_NOT_OURS.split())
    assert "Карточку не принимайте" not in vapi.CHIEF_NOT_OURS
