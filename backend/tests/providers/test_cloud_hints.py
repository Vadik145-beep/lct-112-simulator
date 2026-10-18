"""The cloud voices, the server decides (#263): on the dispatcher's words heard in a cloud call
the server makes the same decisions as without the cloud — the misheard house, the order
taken, «машин нет», the head finding a vehicle — keeps them as hidden turns the truth of the
card reads, and hands them to Vapi as a note in the middle of the call."""

from __future__ import annotations

from types import SimpleNamespace

from app.dialog import officer
from app.domain.scenarios import misheard, no_vehicles, staff
from app.models import Attempt
from app.telephony import vapi
from tests.domain.evaluation.helpers import card_scenario
from tests.providers.test_officer_misheard import new_call

NAME = "card_moek_1_net_otopleniya"  # улица Молостовых, 10, к. 1
ADDRESS = "Нет отопления в трёх домах, дом 10 корпус 1 по улице Молостовых, пострадавших нет."


def hear(scenario, attempt: Attempt, call: dict, text: str) -> str | None:
    """A phrase of the dispatcher as the cloud transcript brings it: the decision is kept as
    ``external_turn`` keeps it (a hidden turn after the phrase)."""
    decided = officer.cloud_decision(scenario, attempt, call, text)
    turns = [{"role": "operator", "text": text, "topics": []}]
    if decided is not None:
        reply, extra = decided
        turns[0]["topics"] = list(reply.operator_topics)
        turns.append(
            {"role": "caller", "text": reply.text, "topics": reply.topics, "hint": True, **extra}
        )
    turns.append({"role": "caller", "text": "(слова облака)", "topics": []})
    call["dialog"] = [*call["dialog"], *turns]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return decided[0].text if decided is not None else None


def test_misheard_and_order_decided_on_the_dispatchers_words() -> None:
    scenario = officer.lesson_scenario(card_scenario(NAME), SimpleNamespace(difficulty=2))
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    assert hear(scenario, attempt, call, ADDRESS) == "Записал: улица Молостовых, дом 12, корпус 1?"
    assert hear(scenario, attempt, call, "Нет, дом десять.").startswith("Понял, дом 10.")
    assert misheard.state(attempt.service_calls).fixed
    # The cloud talks on its own until the order; the order is the server's to take.
    assert hear(scenario, attempt, call, "Горячая вода есть.") is None
    assert hear(scenario, attempt, call, "Высылайте бригаду.").startswith("Принял, выезжаем. Наряд")
    assert officer.order_given(attempt, scenario)


def test_no_vehicles_and_the_head_decided_by_the_server() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    chief = {**new_call("moek"), no_vehicles.PLAN_KEY: no_vehicles.PLAN_NO_VEHICLES}
    attempt = Attempt(service_calls=[chief])
    hear(scenario, attempt, chief, ADDRESS)  # misheard first on difficulty 3
    said = hear(scenario, attempt, chief, "Нет, дом 10.")
    assert said == f"Понял, дом 10. Исправил, записал. {no_vehicles.SHORTAGE}"
    assert hear(scenario, attempt, chief, "Выезжайте.") == no_vehicles.STILL_NONE
    assert no_vehicles.blocked(attempt.service_calls)
    head = {**new_call("moek"), "kind": officer.KIND_STAFF, "role": staff.ROLE_HEAD}
    attempt.service_calls = [*attempt.service_calls, head]
    said = "Докладываю: машин у бригады нет, а на Молостовых нет отопления."
    assert hear(scenario, attempt, head, said) == no_vehicles.RESOLVED
    assert not no_vehicles.blocked(attempt.service_calls)


def test_hidden_turns_are_hints_not_phrases() -> None:
    call = {"dialog": [{"role": "operator", "text": "a"}, {"role": "caller", "text": "b"}]}
    assert officer.hint_of(call) is None
    call["dialog"].append({"role": "caller", "text": "Записал: дом 12?", "hint": True})
    assert officer.hint_of(call) == "Записал: дом 12?"


def test_hint_message_and_control_url() -> None:
    body = vapi.hint_message("Записал: дом 12?")
    assert body["type"] == "add-message"
    assert body["message"]["role"] == "system"
    assert "«Записал: дом 12?»" in body["message"]["content"]
    assert body["message"]["content"].startswith(vapi.HINT_PREFIX)
    message = {"call": {"id": "c1", "monitor": {"controlUrl": "https://ctl/1"}}}
    assert vapi.control_url(message) == "https://ctl/1"
    assert vapi.control_url({"call": {"id": "c1"}}) is None


def test_the_people_of_the_card_take_control_and_know_their_duties() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    chief = officer.call_scenario(scenario, new_call("moek"), Attempt(service_calls=[]))
    assistant = vapi.build_assistant(chief)
    assert assistant["monitorPlan"] == {"controlEnabled": True}
    prompt = vapi.role_prompt(chief)
    assert prompt.startswith("Ты начальник аварийной бригады")
    assert "чего не хватает" in prompt and "Стоп, короче" in prompt
    assert "Служебная подсказка тренажёра" in prompt
    head_call = {**new_call("moek"), "kind": officer.KIND_STAFF, "role": staff.ROLE_HEAD}
    head = officer.call_scenario(scenario, head_call, Attempt(service_calls=[]))
    assert "сверх" in vapi.role_prompt(head) or "не по силам" in vapi.role_prompt(head)


def test_misheard_house_and_no_vehicles_are_said_word_for_word() -> None:
    """On the stand the cloud ignored these two notes (17.10.2026): they go as «say»."""
    misheard_call = {
        "dialog": [
            {"role": "operator", "text": ADDRESS},
            {"role": "caller", "text": "Записал: …, дом 12?", "topics": ["misheard"], "hint": True},
        ]
    }
    assert officer.hint_is_verbatim(misheard_call)
    order_call = {
        "dialog": [
            {"role": "operator", "text": "Выезжайте."},
            {"role": "caller", "text": "Принял, выезжаем.", "topics": ["confirm"], "hint": True},
        ]
    }
    assert not officer.hint_is_verbatim(order_call)
    assert vapi.say_message("Записал: дом 12?") == {
        "type": "say",
        "content": "Записал: дом 12?",
        "endCallAfterSpoken": False,
    }
    note = vapi.said_note("Записал: дом 12?")
    assert note["type"] == "add-message" and note["triggerResponseEnabled"] is False
    assert "Ты только что сказал диспетчеру: «Записал: дом 12?»" in note["message"]["content"]


def test_the_chief_knows_his_truth_from_the_prompt() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 2})
    chief_call = {**new_call("moek"), no_vehicles.PLAN_KEY: no_vehicles.PLAN_NO_VEHICLES}
    attempt = Attempt(service_calls=[chief_call])
    chief = officer.call_scenario(scenario, chief_call, attempt)
    assert chief.caller.facts[no_vehicles.FACT_KEY] == no_vehicles.FACT
    prompt = vapi.role_prompt(chief)
    assert "свободных машин нет" in prompt
    # From difficulty 2 the misheard house is the server's to say, not the cloud's.
    assert vapi.CHIEF_LEAVES_ADDRESS in prompt
    easy = officer.call_scenario(card_scenario(NAME), new_call("moek"), Attempt(service_calls=[]))
    assert no_vehicles.FACT_KEY not in easy.caller.facts
    assert "ОДИН раз повторяешь вслух адрес" in vapi.role_prompt(easy)


def test_no_prompt_tells_the_cloud_to_answer_with_nothing() -> None:
    """«Не отвечаешь ничего» made the cloud say «Молчу.» on «понял» (stand 17.10.2026)."""
    for prompt in (
        vapi.OFFICER_PROMPT,
        vapi.OFFICER_REPEAT_PROMPT,
        vapi.CHIEF_PROMPT,
        vapi.SQUAD_PROMPT,
    ):
        assert "не отвечаешь ничего" not in prompt.lower()
        assert "«Принято»" in prompt
