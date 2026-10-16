"""Per-user access to the 112 operator mode: the administrator grants and takes it away;
without it a teacher neither creates nor sees call-intake lessons and scenarios."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.api.conftest import DATA_DIR
from tests.api.test_sessions import group_id
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


@pytest.fixture
async def restore_access(admin_engine: AsyncEngine):
    yield
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET call_intake_access = true WHERE login = 'teacher1'")
        )


async def _set_access(client: AsyncClient, admin: dict, login_: str, value: bool) -> dict:
    users = (await client.get("/api/admin/users", headers=bearer(admin))).json()
    target = next(u for u in users if u["login"] == login_)
    r = await client.patch(
        f"/api/admin/users/{target['id']}",
        headers=bearer(admin),
        json={"call_intake_access": value},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def test_teacher_without_access_cannot_use_call_intake(
    client: AsyncClient, restore_access
) -> None:
    admin = await login(client, "admin")
    assert (await _set_access(client, admin, "teacher1", False))["call_intake_access"] is False

    teacher = await login(client, "teacher1")
    me = (await client.get("/api/me", headers=bearer(teacher))).json()
    assert me["call_intake_access"] is False

    lesson = {
        "title": "Приём вызова",
        "mode": "call_intake",
        "group_id": await group_id(),
        "difficulty": 3,
        "norm_seconds": 60,
        "pass_threshold": 70,
    }
    r = await client.post("/api/sessions", headers=bearer(teacher), json=lesson)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "call_intake_forbidden"
    r = await client.post(
        "/api/sessions/preview",
        headers=bearer(teacher),
        json={"mode": "call_intake", "card_source": "scenarios", "difficulty": 3},
    )
    assert r.status_code == 403

    r = await client.post(
        "/api/scenarios/generate",
        headers=bearer(teacher),
        json={"kind": "call_intake", "phrase": "Пожар в квартире на третьем этаже"},
    )
    assert r.status_code == 403
    r = await client.post(
        "/api/scenarios/generate",
        headers=bearer(teacher),
        json={"kind": "card_response", "both_kinds": True, "phrase": "Пожар в квартире"},
    )
    assert r.status_code == 403

    listed = (await client.get("/api/scenarios", headers=bearer(teacher))).json()
    assert listed["total"] > 0
    assert {s["kind"] for s in listed["items"]} == {"card_response"}
    only_112 = (await client.get("/api/scenarios?kind=call_intake", headers=bearer(teacher))).json()
    assert only_112["total"] == 0

    # The card-response lesson is not affected.
    r = await client.post(
        "/api/sessions",
        headers=bearer(teacher),
        json={**lesson, "mode": "card_response", "service_profile": ["territorial_oiv"]},
    )
    assert r.status_code == 201, r.text


async def test_access_granted_back_restores_call_intake(
    client: AsyncClient, admin_engine: AsyncEngine, restore_access
) -> None:
    admin = await login(client, "admin")
    await _set_access(client, admin, "teacher1", False)
    assert (await _set_access(client, admin, "teacher1", True))["call_intake_access"] is True

    teacher = await login(client, "teacher1")
    r = await client.post(
        "/api/sessions/preview",
        headers=bearer(teacher),
        json={"mode": "call_intake", "card_source": "scenarios", "difficulty": 3},
    )
    assert r.status_code == 200, r.text
    kinds = {
        s["kind"]
        for s in (await client.get("/api/scenarios", headers=bearer(teacher))).json()["items"]
    }
    assert kinds == {"call_intake", "card_response"}

    async with admin_engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT details FROM audit_log WHERE action = 'user.update' "
                "ORDER BY id DESC LIMIT 2"
            )
        )
        details = [r[0] for r in rows]
    assert {"call_intake_access": True} in details
    assert {"call_intake_access": False} in details
