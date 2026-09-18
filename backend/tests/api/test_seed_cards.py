"""The starter set of card-response scenarios for the major city services (docs/BUGS.md 6):
every major service can run a lesson out of the box, the cards pass the evaluation engine,
and the «other region» card is scored as a reject."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import MODE_CARD_RESPONSE, SCENARIO_APPROVED, Scenario
from tests.api.conftest import DATA_DIR
from tests.api.test_sessions import create_session, journal, set_status, start
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

MAJOR_SERVICES = ("gkh", "mosvodokanal", "moek", "gormost", "mosgaz", "territorial_oiv")


async def test_every_major_service_has_starter_cards() -> None:
    async with SessionLocal() as session:
        rows = await session.execute(
            select(Scenario.service_code, func.count())
            .where(Scenario.kind == MODE_CARD_RESPONSE, Scenario.status == SCENARIO_APPROVED)
            .group_by(Scenario.service_code)
        )
    counts = dict(rows.all())
    missing = [s for s in MAJOR_SERVICES if counts.get(s, 0) == 0]
    assert not missing, f"нет стартовых карточек для служб: {missing}"
    assert counts["gkh"] >= 3


async def test_gkh_lesson_runs_a_starter_card_to_the_end(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(
        client, teacher, title="ЖКХ, занятие 1", difficulty=1, service_profile=["gkh"]
    )
    assert created["queue"], "профиль gkh должен дать очередь"
    assert all(s["service_code"] == "gkh" for s in created["queue"])
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    item = (await journal(client, student, created["id"]))["items"][0]
    attempt_id = item["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(student))
    assert r.status_code == 200, r.text
    assert r.json()["card"]["incident"]["type_code"].startswith("14.")
    for status, extra in (
        ("accepted", {}),
        ("response_started", {"order_number": "ЖКХ-118", "comment": "Направлен сантехник ОДС-4"}),
        ("arrived", {"comment": "Слесарь на месте"}),
        ("works_started", {"comment": "Перекрыт стояк, устранение течи"}),
        ("works_done", {"comment": "Заменён участок стояка, вода подана, подъезд осушен"}),
    ):
        r = await set_status(client, student, attempt_id, status=status, **extra)
        assert r.status_code == 200, (status, r.text)
    attempt = r.json()["attempt"]
    assert attempt["state"] == "evaluated"
    assert attempt["evaluation"]["passed"] is True, attempt["evaluation"]


async def test_other_region_card_expects_a_reject(client: AsyncClient) -> None:
    async with SessionLocal() as session:
        scenario = await session.scalar(
            select(Scenario).where(Scenario.seed_key == "card_gkh_4_chuzhoy_region")
        )
        assert scenario is not None
        scenario_id = str(scenario.id)
    teacher = await login(client, "teacher1")
    created = await create_session(
        client, teacher, difficulty=2, service_profile=["gkh"], scenario_ids=[scenario_id]
    )
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    attempt_id = (await journal(client, student, created["id"]))["items"][0]["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(student))
    assert "Московская область" in r.json()["card"]["address"]["text"]
    # Accepting a card of another region is the wrong decision.
    r = await set_status(client, student, attempt_id, status="accepted")
    assert r.status_code == 200
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(student))
    evaluation = r.json()["attempt"]["evaluation"]
    assert evaluation["passed"] is False
    assert "wrong_accept_unfixed" in {e["code"] for e in evaluation["errors"]}
