"""Administrator API (wave 9): users with hidden names, services, state of the system and
notifications, audit log with the integrity check, backups, settings; and the limits of the
role — no evaluations, no scenarios, no hand in a running lesson."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.admin import health
from app.config import get_settings
from app.db import SessionLocal
from app.models import AdminNotification
from tests.api.conftest import DATA_DIR
from tests.api.test_overrides import evaluated_attempt
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


async def _actions(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT action FROM audit_log ORDER BY id"))
        return [r[0] for r in rows]


@pytest.fixture
async def cleanup_users(admin_engine: AsyncEngine):
    yield
    async with admin_engine.begin() as conn:
        await conn.execute(text("DELETE FROM users WHERE login LIKE 'adm-test-%'"))
        await conn.execute(text("DELETE FROM admin_notifications"))
        await conn.execute(text("DELETE FROM backups"))
        await conn.execute(text("DELETE FROM settings WHERE key <> 'telephony'"))


# ---------------------------------------------------------------- users


async def test_users_hidden_names_reveal_is_audited(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    admin = await login(client, "admin")
    r = await client.get("/api/admin/users", headers=bearer(admin))
    assert r.status_code == 200, r.text
    users = r.json()
    teacher = next(u for u in users if u["login"] == "teacher1")
    # Initials only; the full name is not in the list at all.
    assert teacher["display_name"] == "И. М. П."
    assert "full_name" not in teacher
    assert not any("Иванова" in str(u) for u in users)

    r = await client.post(f"/api/admin/users/{teacher['id']}/reveal", headers=bearer(admin))
    assert r.status_code == 200, r.text
    assert r.json()["full_name"] == "Иванова Мария Петровна"
    assert "user.reveal" in await _actions(admin_engine)

    r = await client.get("/api/admin/users", headers=bearer(admin), params={"role": "student"})
    assert {u["role"] for u in r.json()} == {"student"}
    r = await client.get("/api/admin/users", headers=bearer(admin), params={"q": "teacher"})
    assert {u["login"] for u in r.json()} == {"teacher1", "teacher2"}

    # Not for the other roles.
    for who in ("teacher1", "student1"):
        token = await login(client, who)
        r = await client.get("/api/admin/users", headers=bearer(token))
        assert r.status_code == 403


async def test_create_block_reset_and_sip(
    client: AsyncClient, admin_engine: AsyncEngine, cleanup_users
) -> None:
    admin = await login(client, "admin")
    r = await client.post(
        "/api/admin/users",
        headers=bearer(admin),
        json={
            "login": "adm-test-student",
            "full_name": "Петров Пётр Петрович",
            "role": "student",
            "service_code": "territorial_oiv",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    user_id = created["user"]["id"]
    password = created["temporary_password"]
    assert len(password) >= 8
    assert created["user"]["must_change_password"] is True
    assert created["user"]["display_name"] == "П. П. П."

    # Duplicate login and an unknown service are refused with a clear message.
    r = await client.post(
        "/api/admin/users",
        headers=bearer(admin),
        json={"login": "ADM-TEST-STUDENT", "full_name": "Х", "role": "student"},
    )
    assert r.status_code == 409
    r = await client.post(
        "/api/admin/users",
        headers=bearer(admin),
        json={"login": "adm-test-x", "full_name": "Х", "role": "student", "service_code": "nope"},
    )
    assert r.status_code == 422
    assert "nope" in r.json()["error"]["message"]

    # The new user logs in with the temporary password and has to change it first.
    r = await client.post(
        "/api/auth/login", json={"login": "adm-test-student", "password": password}
    )
    assert r.status_code == 200, r.text
    fresh = r.json()
    assert fresh["user"]["must_change_password"] is True
    r = await client.get("/api/me/assignments", headers=bearer(fresh))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "password_change_required"
    r = await client.post(
        "/api/auth/change-password",
        headers=bearer(fresh),
        json={"old_password": password, "new_password": "NewPassword123"},
    )
    assert r.status_code == 200, r.text
    student_token = r.json()

    # SIP account for the trainee (not for a teacher).
    r = await client.post(f"/api/admin/users/{user_id}/sip", headers=bearer(admin))
    assert r.status_code == 200, r.text
    assert r.json()["login"] == "phone-adm-test-student"
    assert r.json()["password"]
    teacher_id = next(
        u["id"]
        for u in (await client.get("/api/admin/users", headers=bearer(admin))).json()
        if u["login"] == "teacher1"
    )
    r = await client.post(f"/api/admin/users/{teacher_id}/sip", headers=bearer(admin))
    assert r.status_code == 409

    # Reset: a new temporary password, the old session is over.
    r = await client.post(f"/api/admin/users/{user_id}/reset-password", headers=bearer(admin))
    assert r.status_code == 200, r.text
    temporary = r.json()["temporary_password"]
    r = await client.get("/api/me", headers=bearer(student_token))
    assert r.status_code == 401
    r = await client.post(
        "/api/auth/login", json={"login": "adm-test-student", "password": temporary}
    )
    assert r.status_code == 200 and r.json()["user"]["must_change_password"] is True

    # Block: login refused, unblock restores it.
    r = await client.patch(
        f"/api/admin/users/{user_id}", headers=bearer(admin), json={"is_blocked": True}
    )
    assert r.status_code == 200 and r.json()["is_blocked"] is True
    r = await client.post(
        "/api/auth/login", json={"login": "adm-test-student", "password": temporary}
    )
    assert r.status_code == 403
    r = await client.patch(
        f"/api/admin/users/{user_id}",
        headers=bearer(admin),
        json={"is_blocked": False, "role": "teacher", "clear_service": True},
    )
    assert r.status_code == 200
    assert r.json()["role"] == "teacher" and r.json()["service_code"] is None

    # The administrator cannot block itself or change its own role.
    me = (await client.get("/api/me", headers=bearer(admin))).json()
    r = await client.patch(
        f"/api/admin/users/{me['id']}", headers=bearer(admin), json={"is_blocked": True}
    )
    assert r.status_code == 409
    r = await client.patch(
        f"/api/admin/users/{me['id']}", headers=bearer(admin), json={"role": "student"}
    )
    assert r.status_code == 409

    actions = await _actions(admin_engine)
    for expected in ("user.create", "user.sip_account", "user.reset_password", "user.update"):
        assert expected in actions


# ---------------------------------------------------------------- services


async def test_services_flags(client: AsyncClient, admin_engine: AsyncEngine) -> None:
    admin = await login(client, "admin")
    r = await client.get("/api/admin/services", headers=bearer(admin))
    assert r.status_code == 200, r.text
    services = r.json()
    code = services[0]["code"]
    before = services[0]["via_arm112"]
    try:
        r = await client.patch(
            f"/api/admin/services/{code}", headers=bearer(admin), json={"via_arm112": not before}
        )
        assert r.status_code == 200, r.text
        assert r.json()["via_arm112"] is (not before)
        assert "service.update" in await _actions(admin_engine)
        r = await client.patch(
            "/api/admin/services/nope", headers=bearer(admin), json={"no_reject": True}
        )
        assert r.status_code == 404
    finally:
        await client.patch(
            f"/api/admin/services/{code}", headers=bearer(admin), json={"via_arm112": before}
        )


# ---------------------------------------------------------------- limits of the role


async def test_admin_cannot_touch_evaluations_scenarios_or_lessons(client: AsyncClient) -> None:
    session_id, attempt_id, _student = await evaluated_attempt(client)
    admin = await login(client, "admin")
    h = bearer(admin)
    # Evaluation and comments.
    r = await client.patch(
        f"/api/attempts/{attempt_id}/evaluation", headers=h, json={"new_total": 1, "reason": "abc"}
    )
    assert r.status_code == 403
    r = await client.post(f"/api/attempts/{attempt_id}/comments", headers=h, json={"text": "x"})
    assert r.status_code == 403
    # A running lesson: no finish, no start, no settings, no report, no monitor.
    for method, path in (
        ("POST", f"/api/sessions/{session_id}/finish"),
        ("POST", f"/api/sessions/{session_id}/start"),
        ("PATCH", f"/api/sessions/{session_id}"),
        ("GET", f"/api/sessions/{session_id}/report"),
        ("GET", f"/api/sessions/{session_id}/monitor"),
        ("POST", "/api/sessions"),
        ("GET", "/api/groups"),
    ):
        r = await client.request(method, path, headers=h, json={} if method != "GET" else None)
        assert r.status_code == 403, (method, path, r.status_code)
    # The trainee's card is not the administrator's either.
    r = await client.post(
        f"/api/attempts/{attempt_id}/status", headers=h, json={"status": "accepted"}
    )
    assert r.status_code in (403, 404)


async def test_admin_cannot_edit_scenarios(client: AsyncClient) -> None:
    admin = await login(client, "admin")
    h = bearer(admin)
    r = await client.put(f"/api/scenarios/{uuid.uuid4()}", headers=h, json={"title": "x"})
    assert r.status_code == 403
    r = await client.post(f"/api/scenarios/{uuid.uuid4()}/approve", headers=h, json={})
    assert r.status_code == 403
    r = await client.post("/api/scenarios/generate", headers=h, json={"phrase": "пожар"})
    assert r.status_code == 403
    r = await client.get("/api/scenarios", headers=h)
    assert r.status_code == 403


# ---------------------------------------------------------------- health and notifications


async def test_health_tiles_and_languagetool_notification(
    client: AsyncClient, admin_engine: AsyncEngine, cleanup_users, monkeypatch
) -> None:
    # LanguageTool is «stopped»: an address nobody listens on.
    monkeypatch.setattr(get_settings(), "languagetool_url", "http://127.0.0.1:9")
    admin = await login(client, "admin")
    r = await client.get("/api/admin/health", headers=bearer(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    tiles = {t["name"]: t for t in data["services"]}
    assert tiles["backend"]["status"] == "ok"
    assert tiles["postgres"]["status"] == "ok"
    assert tiles["redis"]["status"] == "ok"
    assert tiles["languagetool"]["status"] == "down"
    assert tiles["llm-dialog"]["status"] == "off"
    assert tiles["asterisk"]["status"] == "off"
    assert data["running_sessions"] >= 0 and "cpu_count" in data["load"]

    # The screen itself opens nothing; the monitor does after two failed passes in a row.
    r = await client.get("/api/admin/notifications", headers=bearer(admin))
    assert r.status_code == 200
    assert not [n for n in r.json() if n["source"] == "languagetool"]
    health._down_streaks.clear()
    await health.run_check_once()
    r = await client.get("/api/admin/notifications", headers=bearer(admin))
    assert not [n for n in r.json() if n["source"] == "languagetool"]
    await health.run_check_once()
    notes = (await client.get("/api/admin/notifications", headers=bearer(admin))).json()
    lt = [n for n in notes if n["source"] == "languagetool"]
    assert len(lt) == 1 and lt[0]["kind"] == "provider_unavailable"
    assert "LanguageTool" in lt[0]["title"]

    # A third pass does not open a second notification for the same source.
    await health.run_check_once()
    r = await client.get("/api/admin/notifications", headers=bearer(admin))
    assert len([n for n in r.json() if n["source"] == "languagetool"]) == 1
    assert (await client.get("/api/admin/health", headers=bearer(admin))).json()[
        "open_notifications"
    ] >= 1

    # Evaluation keeps working meanwhile: grammar «не проверено», the total is there.
    _session_id, attempt_id, student = await evaluated_attempt(client)
    mine = (await client.get(f"/api/attempts/{attempt_id}", headers=bearer(student))).json()
    assert mine["evaluation"]["total"] is not None
    assert mine["evaluation"]["components"]["grammar"]["status"] == "not_checked"

    # Acknowledged: gone from the open list, written to the audit.
    r = await client.post(f"/api/admin/notifications/{lt[0]['id']}/ack", headers=bearer(admin))
    assert r.status_code == 200 and r.json()["acknowledged_at"] is not None
    r = await client.get("/api/admin/notifications", headers=bearer(admin))
    assert not [n for n in r.json() if n["source"] == "languagetool"]
    assert "notification.ack" in await _actions(admin_engine)
    r = await client.post(f"/api/admin/notifications/{uuid.uuid4()}/ack", headers=bearer(admin))
    assert r.status_code == 404
    async with SessionLocal() as session:
        assert await session.get(AdminNotification, uuid.UUID(lt[0]["id"])) is not None


# ---------------------------------------------------------------- audit


async def test_audit_page_filters_and_verify(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    await login(client, "student1")
    await login(client, "teacher1")
    admin = await login(client, "admin")
    r = await client.get("/api/admin/audit", headers=bearer(admin))
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["total"] >= 3
    assert page["items"][0]["action"] == "login"
    assert "login" in page["actions"]
    r = await client.get(
        "/api/admin/audit", headers=bearer(admin), params={"actor_login": "student1"}
    )
    rows = r.json()["items"]
    assert rows and all(row["actor_login"] == "student1" for row in rows)
    r = await client.get(
        "/api/admin/audit", headers=bearer(admin), params={"actor_login": "nobody"}
    )
    assert r.json()["total"] == 0
    r = await client.get("/api/admin/audit", headers=bearer(admin), params={"action": "logout"})
    assert r.json()["total"] == 0
    r = await client.get("/api/admin/audit", headers=bearer(admin), params={"per_page": 7})
    assert r.status_code == 422

    r = await client.post("/api/admin/audit/verify", headers=bearer(admin))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True and r.json()["broken_id"] is None

    # Tampering with the owner role breaks the chain, and the check says where.
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE audit_log SET action = 'login_demo' WHERE id = 2"))
    r = await client.post("/api/admin/audit/verify", headers=bearer(admin))
    assert r.status_code == 200
    assert r.json()["ok"] is False and r.json()["broken_id"] == 2
    assert "нарушена" in r.json()["message"]
    teacher = await login(client, "teacher1")
    r = await client.post("/api/admin/audit/verify", headers=bearer(teacher))
    assert r.status_code == 403


# ---------------------------------------------------------------- backups


async def test_backup_request_and_list(
    client: AsyncClient, admin_engine: AsyncEngine, cleanup_users, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    admin = await login(client, "admin")
    (tmp_path / "trainer-20260921-030000.dump").write_bytes(b"PGDMP")
    r = await client.get("/api/admin/backups", headers=bearer(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["service_alive"] is False
    assert data["schedule_time"] == "03:00" and data["keep"] == 14
    assert [b["file_name"] for b in data["items"]] == ["trainer-20260921-030000.dump"]
    assert data["items"][0]["kind"] == "scheduled"

    r = await client.post("/api/admin/backups", headers=bearer(admin))
    assert r.status_code == 202, r.text
    backup_id = r.json()["id"]
    assert r.json()["status"] == "requested"
    request_file = tmp_path / "requests" / f"{backup_id}.request"
    assert request_file.exists()
    assert "backup.request" in await _actions(admin_engine)

    # The backup service answers: the row becomes done with the file.
    request_file.unlink()
    (tmp_path / "trainer-20260922-101010-manual.dump").write_bytes(b"PGDMP" * 10)
    (tmp_path / "requests" / f"{backup_id}.done").write_text("trainer-20260922-101010-manual.dump")
    (tmp_path / ".alive").write_text("")
    r = await client.get("/api/admin/backups", headers=bearer(admin))
    data = r.json()
    assert data["service_alive"] is True
    manual = next(b for b in data["items"] if b["id"] == backup_id)
    assert manual["status"] == "done" and manual["size_bytes"] == 50
    assert manual["kind"] == "manual"
    assert len(data["items"]) == 2

    # A failed one carries the error.
    r = await client.post("/api/admin/backups", headers=bearer(admin))
    failed_id = r.json()["id"]
    (tmp_path / "requests" / f"{failed_id}.failed").write_text("pg_dump: connection refused")
    r = await client.get("/api/admin/backups", headers=bearer(admin))
    failed = next(b for b in r.json()["items"] if b["id"] == failed_id)
    assert failed["status"] == "failed" and "connection refused" in failed["error"]

    teacher = await login(client, "teacher1")
    r = await client.post("/api/admin/backups", headers=bearer(teacher))
    assert r.status_code == 403


# ---------------------------------------------------------------- settings


async def test_settings_sections(
    client: AsyncClient, admin_engine: AsyncEngine, cleanup_users, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(get_settings(), "backup_dir", str(tmp_path))
    admin = await login(client, "admin")
    r = await client.get("/api/admin/settings", headers=bearer(admin))
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"telephony", "logging", "backups", "training"}
    assert data["logging"]["level"] == "WARNING"  # tests run with LOG_LEVEL=WARNING
    assert data["training"]["unfinished_seconds"] == 48 * 3600

    r = await client.patch(
        "/api/admin/settings",
        headers=bearer(admin),
        json={
            "logging": {"level": "DEBUG"},
            "backups": {"time": "04:30", "keep": 7},
            "training": {"unfinished_seconds": 3600},
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["logging"]["level"] == "DEBUG"
    assert data["backups"] == {"time": "04:30", "keep": 7}
    assert data["training"]["unfinished_seconds"] == 3600
    assert (tmp_path / ".schedule").read_text() == "BACKUP_TIME=04:30\nBACKUP_KEEP=7\n"
    assert "settings.update" in await _actions(admin_engine)

    # Validation: a bad time, a bad level, a threshold out of range.
    r = await client.patch(
        "/api/admin/settings", headers=bearer(admin), json={"backups": {"time": "25:00"}}
    )
    assert r.status_code == 422
    r = await client.patch(
        "/api/admin/settings", headers=bearer(admin), json={"logging": {"level": "TRACE"}}
    )
    assert r.status_code == 422
    r = await client.patch(
        "/api/admin/settings", headers=bearer(admin), json={"training": {"unfinished_seconds": 5}}
    )
    assert r.status_code == 422

    # A new lesson takes the threshold from the settings when the teacher leaves it empty.
    teacher = await login(client, "teacher1")
    groups = (await client.get("/api/groups", headers=bearer(teacher))).json()
    r = await client.post(
        "/api/sessions",
        headers=bearer(teacher),
        json={"title": "Порог из настроек", "group_id": groups[0]["id"]},
    )
    assert r.status_code == 201, r.text
    assert r.json()["unfinished_seconds"] == 3600

    # Back to the defaults so the log level of the test process is restored.
    r = await client.patch(
        "/api/admin/settings",
        headers=bearer(admin),
        json={"logging": {"level": "WARNING"}, "training": {"unfinished_seconds": 48 * 3600}},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------- security


async def test_no_cors_for_foreign_origin(client: AsyncClient) -> None:
    """CORS only for the own origin (PRD 14): a foreign origin gets no allow header, so the
    browser refuses the response."""
    r = await client.options(
        "/api/auth/login",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in r.headers
    r = await client.get("/api/config", headers={"Origin": "https://evil.example"})
    assert r.status_code == 200
    assert "access-control-allow-origin" not in r.headers
