"""Calls to the trainee's phone (docs/MULTIFON.md) through the API: the trainee enters his own
number, the teacher turns the phone on for a lesson only where telephony and MultiFon are
configured, the trainee's lesson says so."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.models import MODE_CALL_INTAKE
from tests.conftest import bearer, login


async def test_trainee_sets_and_clears_his_phone(client: AsyncClient) -> None:
    token = await login(client, "student1")
    saved = await client.put(
        "/api/me/phone", headers=bearer(token), json={"phone": "8 (922) 000-00-01"}
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["phone"] == "79220000001"
    me = await client.get("/api/me", headers=bearer(token))
    assert me.json()["phone"] == "79220000001"

    bad = await client.put("/api/me/phone", headers=bearer(token), json={"phone": "12345"})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "bad_phone"

    cleared = await client.put("/api/me/phone", headers=bearer(token), json={"phone": ""})
    assert cleared.status_code == 200
    assert cleared.json()["phone"] is None


async def _lesson_body(client: AsyncClient, token: dict) -> dict:
    groups = await client.get("/api/groups", headers=bearer(token))
    rows = groups.json()
    group_id = (rows["items"] if isinstance(rows, dict) else rows)[0]["id"]
    return {
        "title": "Звонки на телефон",
        "mode": MODE_CALL_INTAKE,
        "group_id": group_id,
        "difficulty": 1,
        "norm_seconds": 30,
        "pass_threshold": 70,
        "dialog_mode": "select",
        "phone_calls": True,
    }


async def test_phone_lessons_need_telephony_and_multifon(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await login(client, "teacher1")
    body = await _lesson_body(client, token)
    settings = get_settings()

    models = await client.get("/api/models", headers=bearer(token))
    assert models.json()["phone"] is False
    refused = await client.post("/api/sessions", headers=bearer(token), json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json()["error"]["code"] == "phone_calls_unavailable"

    monkeypatch.setattr(settings, "telephony_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "multifon_user", "79227816205")
    models = await client.get("/api/models", headers=bearer(token))
    assert models.json()["phone"] is True
    created = await client.post("/api/sessions", headers=bearer(token), json=body)
    assert created.status_code == 201, created.text
    assert created.json()["phone_calls"] is True
    session_id = created.json()["id"]

    # The trainee of the group sees that the lesson rings his phone.
    student = await login(client, "student1")
    assignments = await client.get("/api/me/assignments", headers=bearer(student))
    assert assignments.status_code == 200, assignments.text
    lesson = next(a for a in assignments.json() if a["id"] == session_id)
    assert lesson["phone_calls"] is True

    # An ordinary lesson stays without the phone.
    plain = await client.post(
        "/api/sessions",
        headers=bearer(token),
        json={**body, "title": "Без телефона", "phone_calls": False},
    )
    assert plain.status_code == 201
    assert plain.json()["phone_calls"] is False
