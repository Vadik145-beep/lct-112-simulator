"""The duty officer in ``select`` (stand, 25.09.2026): a dispatcher who passes several facts in
one phrase is not asked to repeat («Прорыв воды… улица Свободы, 42…» → «Повторите, плохо
слышно» twice), a fact already passed is not requested again, the squad's number is named when
asked, and thanks are not «не ко мне»."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.evaluation.schemas import CardResponseScenario, DialogTurn, ServiceCallRef
from app.domain.scenarios import officers
from app.providers.dialog import ROLE_OFFICER, DialogContext, SelectDialog
from tests.providers.fake_model import FakeModel

SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "data" / "seed" / "scenarios"

# The dispatcher's phrases from the stand, as the recogniser wrote them.
ADDRESS_AND_INCIDENT = (
    "Прорыв воды, дом, много квартиры, на квартире подъезд подвал, находится по улице "
    "Свободы, 42 корпуса, 2 подъезд, 1 этаж 3."
)


@pytest.fixture
def ctx() -> DialogContext:
    card = CardResponseScenario.model_validate(
        json.loads((SCENARIOS_DIR / "card_17-1_pozharnaya_signalizaciya.json").read_text("utf-8"))
    )
    # Address, incident type and the injured: the default facts of a service call.
    ref = ServiceCallRef(service="gkh")
    officer = officers.officer_scenario(card, ref, "Городское хозяйство (ЕДЦ ЖКХ)")
    return DialogContext(scenario=officer, conversation_id="call-1", role=ROLE_OFFICER)


def reply_with(ctx: DialogContext, text: str):
    return next(r for r in ctx.scenario.replies if r.text == text)


async def test_several_facts_are_taken_and_the_next_one_is_asked(ctx: DialogContext) -> None:
    order = next(r for r in ctx.scenario.replies if r.topic == "order_number")
    model = FakeModel([{"reply_id": order.id}])  # a choice about a fact nobody passed
    reply = await SelectDialog(model).reply(ctx, ADDRESS_AND_INCIDENT)
    assert reply.text == "Пострадавшие есть?"
    assert reply.topics == ["injured"]
    assert reply.method == "select+facts"
    # The dispatcher's turn keeps the facts they passed, not the one asked about.
    assert set(reply.operator_topics) >= {"address", "incident_type"}
    assert "injured" not in reply.operator_topics


async def test_nothing_chosen_still_asks_the_next_fact(ctx: DialogContext) -> None:
    reply = await SelectDialog(FakeModel([{"reply_id": None}])).reply(ctx, ADDRESS_AND_INCIDENT)
    assert reply.text == "Пострадавшие есть?"


async def test_the_models_question_about_a_missing_fact_is_kept(ctx: DialogContext) -> None:
    access = reply_with(ctx, "Доступ на объект есть? Кто встретит бригаду?")
    ctx.scenario.required_topics.append("access")
    reply = await SelectDialog(FakeModel([{"reply_id": access.id}])).reply(
        ctx, ADDRESS_AND_INCIDENT
    )
    assert reply.reply_id == access.id
    assert reply.method == "select"


async def test_a_confirmation_of_a_fact_not_passed_is_not_kept(ctx: DialogContext) -> None:
    said_injured = reply_with(ctx, "Принято, по пострадавшим понял.")
    reply = await SelectDialog(FakeModel([{"reply_id": said_injured.id}])).reply(
        ctx, ADDRESS_AND_INCIDENT
    )
    assert reply.text == "Пострадавшие есть?"


async def test_facts_passed_earlier_in_the_call_are_not_asked_again(ctx: DialogContext) -> None:
    ctx.history += [
        DialogTurn(role="operator", text="Пострадавших нет", topics=["injured"]),
        DialogTurn(role="caller", text="Принято, по пострадавшим понял.", topics=["injured"]),
    ]
    reply = await SelectDialog(FakeModel([{"reply_id": None}])).reply(ctx, ADDRESS_AND_INCIDENT)
    assert reply.topics == ["confirm"]
    assert reply.method == "select+facts"


async def test_one_fact_is_handled_as_before(ctx: DialogContext) -> None:
    order = next(r for r in ctx.scenario.replies if r.topic == "order_number")
    reply = await SelectDialog(FakeModel([{"reply_id": order.id}])).reply(
        ctx, "Адрес: улица Свободы, дом 42"
    )
    assert reply.topics == ["address"]
    assert reply.method == "select+keywords"


async def test_a_call_without_required_facts_keeps_the_general_rule(ctx: DialogContext) -> None:
    """A squad's report has no facts to collect: several topics still mean «повторите»."""
    ctx.scenario.required_topics.clear()
    order = next(r for r in ctx.scenario.replies if r.topic == "order_number")
    reply = await SelectDialog(FakeModel([{"reply_id": order.id}])).reply(ctx, ADDRESS_AND_INCIDENT)
    assert reply.topics == ["repeat"]


async def test_a_missing_address_is_requested_not_confirmed(ctx: DialogContext) -> None:
    """«Адрес принял. Подъезд, этаж известны?» ends with a question mark but confirms."""
    reply = await SelectDialog(FakeModel([{"reply_id": None}])).reply(
        ctx, "Прорыв воды в подвале, пострадавших нет"
    )
    assert reply.text == "Назовите адрес: улица, дом, корпус."


async def test_an_address_just_passed_is_confirmed_not_requested(ctx: DialogContext) -> None:
    ask = reply_with(ctx, "Назовите адрес: улица, дом, корпус.")
    reply = await SelectDialog(FakeModel([{"reply_id": ask.id}])).reply(
        ctx, "Адрес: Профсоюзная улица, дом 12."
    )
    assert reply.text == "Адрес принял. Подъезд, этаж известны?"
    assert reply.method == "select+officer"


async def test_a_fact_passed_earlier_is_not_requested_again(ctx: DialogContext) -> None:
    ctx.history += [
        DialogTurn(role="operator", text="Адрес: Профсоюзная, 12", topics=["address"]),
        DialogTurn(role="caller", text="Адрес принял. Подъезд, этаж известны?", topics=["address"]),
    ]
    ask = reply_with(ctx, "Назовите адрес: улица, дом, корпус.")
    reply = await SelectDialog(FakeModel([{"reply_id": ask.id}])).reply(
        ctx, "Затопление подвала, пострадавших нет"
    )
    # Address, incident and the injured are all passed now: the officer takes the call.
    assert reply.topics == ["confirm"]


async def test_a_service_question_about_a_passed_fact_is_kept(ctx: DialogContext) -> None:
    """«Стояк, кровля или подвал?» asks more than the dispatcher said: not a deaf request."""
    question = reply_with(ctx, "Стояк, кровля или подвал? Квартиры заливает?")
    reply = await SelectDialog(FakeModel([{"reply_id": question.id}])).reply(
        ctx, "У нас прорыв воды."
    )
    assert reply.reply_id == question.id


async def test_the_squad_number_is_named_when_asked(ctx: DialogContext) -> None:
    unknown = next(r for r in ctx.scenario.replies if r.topic == "unknown")
    reply = await SelectDialog(FakeModel([{"reply_id": unknown.id}])).reply(
        ctx, "Какой наряд выезжает?"
    )
    assert reply.topics == ["order_number"]
    assert reply.text.startswith("Наряд ")
    assert reply.text.split()[1].rstrip(",").isdigit()


async def test_where_is_the_squad_is_not_a_question_about_the_number(ctx: DialogContext) -> None:
    progress = next(r for r in ctx.scenario.replies if r.topic == "progress")
    reply = await SelectDialog(FakeModel([{"reply_id": progress.id}])).reply(ctx, "Где наряд?")
    assert reply.reply_id == progress.id


async def test_thanks_are_confirmed_not_off_topic(ctx: DialogContext) -> None:
    unknown = next(r for r in ctx.scenario.replies if r.topic == "unknown")
    reply = await SelectDialog(FakeModel([{"reply_id": unknown.id}])).reply(
        ctx, "Спасибо, до свидания."
    )
    assert reply.topics == ["confirm"]
    assert reply.method == "select+officer"


async def test_an_off_topic_phrase_is_still_off_topic(ctx: DialogContext) -> None:
    unknown = next(r for r in ctx.scenario.replies if r.topic == "unknown")
    reply = await SelectDialog(FakeModel([{"reply_id": unknown.id}])).reply(
        ctx, "Какая завтра погода?"
    )
    assert reply.reply_id == unknown.id


async def test_the_fact_of_the_phrase_is_confirmed_not_the_one_asked(ctx: DialogContext) -> None:
    """Address passed earlier, the model asks it again on «Затопление подвала»: the officer
    confirms the incident, not the address."""
    ctx.history += [
        DialogTurn(role="operator", text="Адрес: Профсоюзная, 12", topics=["address"]),
        DialogTurn(role="caller", text="Адрес принял. Подъезд, этаж известны?", topics=["address"]),
    ]
    ask = reply_with(ctx, "Назовите адрес: улица, дом, корпус.")
    reply = await SelectDialog(FakeModel([{"reply_id": ask.id}])).reply(ctx, "Затопление подвала.")
    assert reply.text == "Понял, происшествие принял."


async def test_where_is_the_squad_gets_the_progress(ctx: DialogContext) -> None:
    confirm = next(r for r in ctx.scenario.replies if r.topic == "confirm")
    reply = await SelectDialog(FakeModel([{"reply_id": confirm.id}])).reply(
        ctx, "Где наряд сейчас?"
    )
    assert reply.topics == ["progress"]


async def test_the_officer_does_not_speak_the_callers_phrases(ctx: DialogContext) -> None:
    """With his own «вне темы» used, the officer says it again rather than «Не знаю, честно»."""
    unknown = next(r for r in ctx.scenario.replies if r.topic == "unknown")
    ctx.used_reply_ids.add(unknown.id)
    reply = await SelectDialog(FakeModel([{"reply_id": None}])).reply(ctx, "Какая завтра погода?")
    assert reply.reply_id == unknown.id
