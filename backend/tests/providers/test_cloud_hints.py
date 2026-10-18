"""The cloud talks on its own, the record decides (#263, decision of 18.10.2026): the server says
nothing to Vapi in the middle of a call — no notes, no «say», no Live Call Control. «Машин нет»,
the hurry and the head's vehicle come through the prompts of the roles; after the call the
record of the cloud's own words decides about the squad: the chief named it, or the head took
a vehicle off a planned job."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.dialog import officer
from app.domain.scenarios import no_vehicles, staff
from app.models import Attempt
from app.telephony import vapi
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
    assert prompt.startswith("Ты начальник аварийной бригады")
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
        "свободных машин нет, все на выездах, наряд не называешь"
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
    assert "ОДИН раз повторяешь вслух адрес" in prompt
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
