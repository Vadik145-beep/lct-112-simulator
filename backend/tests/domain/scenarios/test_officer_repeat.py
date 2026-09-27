"""Repeat call to a service officer (замечание пользователя 27.09.2026): the card was passed
on an earlier call, the officer does not take it again."""

from __future__ import annotations

from app.domain.evaluation.schemas import ServiceCallRef
from app.domain.scenarios import officers
from app.telephony import vapi
from tests.domain.evaluation.helpers import card_scenario


def test_repeat_officer_opens_with_the_squad_and_asks_nothing() -> None:
    scenario = card_scenario()
    ref = ServiceCallRef(service="mosgaz")
    first = officers.officer_scenario(scenario, ref, "Мосгаз", "works_started")
    again = officers.officer_scenario(scenario, ref, "Мосгаз", "works_started", repeat=True)

    assert first.caller.opening == officers.greeting("mosgaz", "Мосгаз")
    assert again.caller.opening.startswith(first.caller.opening)
    assert "уже приняли" in again.caller.opening
    assert officers.PROGRESS_REPLIES["works_started"] in again.caller.opening
    assert again.required_topics == []
    assert first.required_topics == list(ref.required_facts)
    assert not [r for r in again.replies if r.text.rstrip().endswith("?")]
    assert [r for r in first.replies if r.text.rstrip().endswith("?")]
    assert officers.REPEAT_FACT_KEY in again.caller.facts
    assert officers.REPEAT_FACT_KEY not in first.caller.facts


def test_cloud_gets_the_repeat_instruction_only_on_a_repeat_call() -> None:
    scenario = card_scenario()
    ref = ServiceCallRef(service="mosgaz")
    first = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived")
    again = officers.officer_scenario(scenario, ref, "Мосгаз", "arrived", repeat=True)
    assert "ПРИНЯТЬ информацию" in vapi.role_prompt(first)
    prompt = vapi.role_prompt(again)
    assert "уже принял от него в прошлом" in prompt
    assert "ПРИНЯТЬ информацию" not in prompt
