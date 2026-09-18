"""Analytics API (PRD 9.7): group analytics with the forecast, the model card, the trainee's
progress, ratings after an evaluation and adaptive card selection."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import SkillRating, User
from tests.api.conftest import DATA_DIR
from tests.api.test_sessions import create_session, group_id, journal, start
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


async def test_group_analytics_on_seed_history(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    r = await client.get(f"/api/analytics/groups/{await group_id()}", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["group_title"] == "Учебная-1" and data["period"]["days"] == 28
    assert data["demo_data"] is True
    assert data["volume"]["students"] == 6 and data["volume"]["attempts"] >= 100
    assert data["volume"]["sessions"] >= 8
    assert {c["mode"] for c in data["heatmap"]} == {"card_response", "call_intake"}
    assert len(data["incident_groups"]) >= 3 and all(g["title"] for g in data["incident_groups"])
    assert len(data["dynamics"]) >= 6
    assert data["errors"] and data["errors"][0]["count"] >= data["errors"][-1]["count"]
    assert 0 < data["errors"][0]["students_share"] <= 1
    assert data["model_available"] is True
    assert len(data["readiness"]) == 6
    for row in data["readiness"]:
        assert row["attempts"] > 0 and 0 <= row["probability"] <= 1
        assert row["risk"] in ("low", "medium", "high") and len(row["reasons"]) == 2
    assert {row["risk"] for row in data["readiness"]} >= {"low", "high"}
    assert "оценённых попыток" in data["summary"]

    # The period narrows the charts but not the forecast.
    r = await client.get(
        f"/api/analytics/groups/{await group_id()}", headers=bearer(teacher), params={"days": 7}
    )
    week = r.json()
    assert week["volume"]["attempts"] < data["volume"]["attempts"]
    assert [x["probability"] for x in week["readiness"]] == [
        x["probability"] for x in data["readiness"]
    ]


async def test_group_analytics_access_and_empty_state(client: AsyncClient) -> None:
    other = await login(client, "teacher2")
    r = await client.get(f"/api/analytics/groups/{await group_id()}", headers=bearer(other))
    assert r.status_code == 403
    student = await login(client, "student1")
    r = await client.get(f"/api/analytics/groups/{await group_id()}", headers=bearer(student))
    assert r.status_code == 403

    r = await client.get(
        f"/api/analytics/groups/{await group_id('Учебная-2')}", headers=bearer(other)
    )
    assert r.status_code == 200, r.text
    empty = r.json()
    assert empty["volume"]["attempts"] == 0 and empty["heatmap"] == [] and empty["errors"] == []
    assert empty["demo_data"] is False
    assert all(x["risk"] == "unknown" and x["probability"] is None for x in empty["readiness"])
    assert "нет" in empty["summary"]


async def test_readiness_model_card(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    r = await client.get("/api/analytics/readiness-model", headers=bearer(teacher))
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["trained"] is True
    assert card["roc_auc"] >= 0.75 and card["brier"] < card["brier_baseline"]
    assert card["n_train"] + card["n_test"] == card["n_total"] == 300
    assert sum(b["count"] for b in card["calibration"]) == card["n_test"]
    assert len(card["features"]) == 8 and all(f["title"] for f in card["features"])
    student = await login(client, "student1")
    r = await client.get("/api/analytics/readiness-model", headers=bearer(student))
    assert r.status_code == 403


async def test_my_progress_carries_ratings_and_dynamics(client: AsyncClient) -> None:
    """«Мой прогресс» (wave 9) gains the analytics: ratings from the weakest group, weekly
    dynamics, the demonstration mark and the tip about the weakest group first."""
    student = await login(client, "student4")
    r = await client.get("/api/me/progress", headers=bearer(student))
    assert r.status_code == 200, r.text
    progress = r.json()
    assert progress["evaluated"] >= 10 and progress["demo_data"] is True
    ratings = progress["ratings"]
    assert ratings and ratings[0]["rating"] <= ratings[-1]["rating"]
    assert all(rt["title"] and rt["n"] > 0 for rt in ratings)
    assert len(progress["dynamics"]) >= 4
    assert progress["recommendations"][0].startswith("Слабое место")
    assert 1 <= len(progress["recommendations"]) <= 4

    fresh = await login(client, "student7")
    empty = (await client.get("/api/me/progress", headers=bearer(fresh))).json()
    assert empty["ratings"] == [] and empty["dynamics"] == [] and empty["demo_data"] is False


async def _set_rating(login_name: str, group: str, mode: str, rating: float) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.login == login_name))
        assert user is not None
        stmt = insert(SkillRating).values(
            student_id=user.id, incident_group=group, mode=mode, rating=rating, n=1
        )
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=["student_id", "incident_group", "mode"],
                set_={"rating": rating},
            )
        )
        await session.commit()


async def _rating(login_name: str, group: str, mode: str) -> SkillRating:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.login == login_name))
        row = await session.get(SkillRating, (user.id, group, mode))
        assert row is not None
        return row


async def test_evaluation_moves_the_rating(client: AsyncClient) -> None:
    teacher = await login(client, "teacher1")
    created = await create_session(client, teacher, difficulty=1, service_profile=["mosgaz"])
    await start(client, teacher, created["id"])
    student = await login(client, "student1")
    await _set_rating("student1", "13", "card_response", 1400)
    before = await _rating("student1", "13", "card_response")
    attempt_id = (await journal(client, student, created["id"]))["items"][0]["attempt_id"]
    r = await client.post(f"/api/attempts/{attempt_id}/finish", headers=bearer(student))
    assert r.status_code == 200, r.text
    after = await _rating("student1", "13", "card_response")
    assert after.n == before.n + 1
    # A card closed without a status scores low: the rating falls.
    assert after.rating < 1400


async def test_adaptive_session_serves_the_weakest_group_first(client: AsyncClient) -> None:
    """student1: strong in gas (13), weak in fire (1). With adaptive selection the first
    card is a fire card even though the easy gas card would otherwise come first."""
    teacher = await login(client, "teacher1")
    await _set_rating("student1", "13", "card_response", 1650)
    await _set_rating("student1", "1", "card_response", 1250)
    profile = ["territorial_oiv", "mosgaz"]
    plain = await create_session(client, teacher, difficulty=2, service_profile=profile)
    adaptive = await create_session(
        client, teacher, difficulty=2, service_profile=profile, adaptive=True
    )
    assert adaptive["adaptive"] is True and plain["adaptive"] is False
    await start(client, teacher, plain["id"])
    await start(client, teacher, adaptive["id"])
    student = await login(client, "student1")
    first_plain = (await journal(client, student, plain["id"]))["items"][0]
    first_adaptive = (await journal(client, student, adaptive["id"]))["items"][0]
    assert "газ" in first_plain["incident_title"].lower()
    assert "газ" not in first_adaptive["incident_title"].lower()

    # Like every other setting, the flag is fixed once the session runs.
    r = await client.patch(
        f"/api/sessions/{plain['id']}", headers=bearer(teacher), json={"adaptive": True}
    )
    assert r.status_code == 409
