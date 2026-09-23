"""Обратный звонок диспетчера заявителю из карточки ДДС (ответ заказчика 23.09.2026):
трубка у телефона карточки поднимает тот же разговор, что и звонок в службу, но на другом
конце заявитель, факты никому не передаются и на оценку звонок не влияет."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db import SessionLocal
from app.dialog import officer
from app.models import MODE_CARD_RESPONSE, Scenario, ScenarioVersion
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.api.test_service_calls import end, say, start
from tests.conftest import login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

CARD = "card_moek_1_net_otopleniya"


async def caller_attempt() -> uuid.UUID:
    return await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="select")


async def current_body() -> tuple[ScenarioVersion, dict]:
    """Действующая версия поставочной карточки: ``current_version`` — номер версии, а не её id."""
    async with SessionLocal() as session:
        scenario = await session.scalar(select(Scenario).where(Scenario.seed_key == CARD))
        version = await session.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == scenario.id,
                ScenarioVersion.version == scenario.current_version,
            )
        )
        return version, dict(version.body)


async def card_caller_name() -> str:
    _version, body = await current_body()
    return str(body["card"]["caller"]["name"])


async def test_dispatcher_calls_the_caller_of_the_card(client: AsyncClient) -> None:
    token = await login(client, "student1")
    attempt_id = await caller_attempt()

    started = await start(client, token, attempt_id, service=officer.CALLER_TARGET)
    assert started.status_code == 200, started.text
    call = started.json()["call"]

    assert call["kind"] == "caller"
    assert call["service"] == officer.CALLER_TARGET
    # В шапке разговора видно, кому звоним: имя заявителя из карточки, а не название службы.
    assert call["service_title"] == await card_caller_name()
    # Заявитель снял трубку и поздоровался сам, номер карточки при этом не звучит.
    assert [t["role"] for t in call["turns"]] == ["caller"]
    assert call["turns"][0]["text"]
    # Передавать заявителю нечего: полоса фактов на таком звонке не нужна.
    assert call["facts_required"] == []
    assert call["facts_passed"] == []


async def test_caller_answers_the_dispatcher(client: AsyncClient) -> None:
    token = await login(client, "student1")
    attempt_id = await caller_attempt()
    call_id = (await start(client, token, attempt_id, service=officer.CALLER_TARGET)).json()[
        "call"
    ]["id"]

    said = await say(client, token, attempt_id, call_id, "Назовите адрес, куда вызывали.")
    assert said.status_code == 200, said.text
    turns = said.json()["call"]["turns"]
    assert [t["role"] for t in turns[-2:]] == ["operator", "caller"]
    assert turns[-1]["text"]
    # Факты звонка в службу на этом разговоре не копятся.
    assert said.json()["call"]["facts_passed"] == []

    hung = await end(client, token, attempt_id, call_id)
    assert hung.status_code == 200, hung.text


async def test_second_call_waits_for_the_first_to_end(client: AsyncClient) -> None:
    token = await login(client, "student1")
    attempt_id = await caller_attempt()
    await start(client, token, attempt_id, service=officer.CALLER_TARGET)

    again = await start(client, token, attempt_id, service="moek")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "call_in_progress"


async def set_caller_phone(phone: str) -> str:
    """Заменяет телефон заявителя в поставочном сценарии и возвращает прежний: сценарии
    между тестами не пересидируются, поэтому тест обязан вернуть карточку как было."""
    async with SessionLocal() as session:
        scenario = await session.scalar(select(Scenario).where(Scenario.seed_key == CARD))
        version = await session.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == scenario.id,
                ScenarioVersion.version == scenario.current_version,
            )
        )
        body = dict(version.body)
        card = dict(body["card"])
        caller = dict(card["caller"])
        was = caller.get("phone") or ""
        caller["phone"] = phone
        version.body = {**body, "card": {**card, "caller": caller}}
        await session.commit()
    return was


async def test_card_without_a_phone_has_nowhere_to_call(client: AsyncClient) -> None:
    """«Если номера нет, то только просто видит службы» — звонить такой карточке некуда."""
    was = await set_caller_phone("")
    try:
        token = await login(client, "student1")
        attempt_id = await caller_attempt()
        refused = await start(client, token, attempt_id, service=officer.CALLER_TARGET)
        assert refused.status_code == 422
        assert refused.json()["error"]["code"] == "no_caller_phone"
    finally:
        await set_caller_phone(was)
