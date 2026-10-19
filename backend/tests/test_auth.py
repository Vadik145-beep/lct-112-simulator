from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from tests.conftest import SEED_PASSWORD, bearer, login


async def test_login_returns_token_user_and_refresh_cookie(client: AsyncClient) -> None:
    data = await login(client, "student1")
    assert data["token_type"] == "bearer"
    assert data["user"]["login"] == "student1"
    assert data["user"]["role"] == "student"
    assert data["user"]["must_change_password"] is False
    assert "refresh_token" in client.cookies


async def test_login_is_case_insensitive_and_trims(client: AsyncClient) -> None:
    data = await login(client, "  Student1 ")
    assert data["user"]["login"] == "student1"


async def test_wrong_password_gives_uniform_error(client: AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"login": "student1", "password": "nope"})
    assert r.status_code == 401
    body = r.json()
    assert body["error"]["code"] == "bad_credentials"
    assert body["error"]["message"] == "Неверный логин или пароль."

    r2 = await client.post("/api/auth/login", json={"login": "no-such-user", "password": "x"})
    assert r2.status_code == 401
    assert r2.json() == body


async def test_five_failures_lock_login_for_fifteen_minutes(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    settings = get_settings()
    for _ in range(settings.login_max_attempts - 1):
        r = await client.post("/api/auth/login", json={"login": "student2", "password": "bad"})
        assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"login": "student2", "password": "bad"})
    assert r.status_code == 423
    assert r.json()["error"]["code"] == "login_locked"

    # Even the correct password is refused while locked.
    r = await client.post("/api/auth/login", json={"login": "student2", "password": SEED_PASSWORD})
    assert r.status_code == 423

    async with admin_engine.connect() as conn:
        locked_until = await conn.scalar(
            text("SELECT locked_until FROM users WHERE login = 'student2'")
        )
    remaining = locked_until - datetime.now(UTC)
    assert (
        timedelta(minutes=settings.login_lock_minutes - 1)
        < remaining
        <= timedelta(minutes=settings.login_lock_minutes)
    )

    # Once the lock expires the correct password works again.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE users SET locked_until = now() - interval '1 second' "
                "WHERE login = 'student2'"
            )
        )
    await login(client, "student2")


async def test_blocked_user_cannot_login(client: AsyncClient, admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE users SET is_blocked = true WHERE login = 'student3'"))
    r = await client.post("/api/auth/login", json={"login": "student3", "password": SEED_PASSWORD})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "user_blocked"


async def test_me_requires_token(client: AsyncClient) -> None:
    r = await client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"

    r = await client.get("/api/me", headers=bearer("garbage"))
    assert r.status_code == 401

    data = await login(client, "teacher1")
    r = await client.get("/api/me", headers=bearer(data))
    assert r.status_code == 200
    assert r.json()["role"] == "teacher"


@pytest.mark.parametrize(
    ("user", "path", "expected"),
    [
        ("student1", "/api/student/cabinet", 200),
        ("student1", "/api/teacher/cabinet", 403),
        ("student1", "/api/admin/cabinet", 403),
        ("teacher1", "/api/teacher/cabinet", 200),
        ("teacher1", "/api/admin/cabinet", 403),
        ("teacher1", "/api/student/cabinet", 403),
        ("admin", "/api/admin/cabinet", 200),
        ("admin", "/api/teacher/cabinet", 403),
    ],
)
async def test_role_access(client: AsyncClient, user: str, path: str, expected: int) -> None:
    data = await login(client, user)
    r = await client.get(path, headers=bearer(data))
    assert r.status_code == expected, r.text
    if expected == 403:
        assert r.json()["error"]["code"] == "forbidden"


async def test_refresh_rotates_and_logout_revokes(client: AsyncClient) -> None:
    await login(client, "student1")
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 200
    assert r.json()["user"]["login"] == "student1"

    r = await client.post("/api/auth/logout")
    assert r.status_code == 200
    r = await client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "refresh_invalid"


async def test_change_password_invalidates_old_tokens(client: AsyncClient) -> None:
    old = await login(client, "student4")
    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": "wrong", "new_password": "Another-Pass-1"},
        headers=bearer(old),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_old_password"

    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": SEED_PASSWORD, "new_password": "Another-Pass-1"},
        headers=bearer(old),
    )
    assert r.status_code == 200
    new = r.json()

    r = await client.get("/api/me", headers=bearer(old))
    assert r.status_code == 401
    r = await client.get("/api/me", headers=bearer(new))
    assert r.status_code == 200

    r = await client.post("/api/auth/login", json={"login": "student4", "password": SEED_PASSWORD})
    assert r.status_code == 401
    await login(client, "student4", "Another-Pass-1")


async def test_must_change_password_blocks_cabinet_until_changed(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("UPDATE users SET must_change_password = true WHERE login = 'student5'")
        )
    data = await login(client, "student5")
    assert data["user"]["must_change_password"] is True

    r = await client.get("/api/student/cabinet", headers=bearer(data))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "password_change_required"

    r = await client.post(
        "/api/auth/change-password",
        json={"old_password": SEED_PASSWORD, "new_password": "Fresh-Pass-22"},
        headers=bearer(data),
    )
    assert r.status_code == 200
    assert r.json()["user"]["must_change_password"] is False
    r = await client.get("/api/student/cabinet", headers=bearer(r.json()))
    assert r.status_code == 200


@pytest.mark.parametrize(
    ("role", "expected_login"),
    [("student", "student1"), ("teacher", "teacher1"), ("admin", "admin")],
)
async def test_demo_login(client: AsyncClient, role: str, expected_login: str) -> None:
    r = await client.post(f"/api/auth/demo/{role}")
    assert r.status_code == 200
    assert r.json()["user"]["login"] == expected_login
    assert r.json()["user"]["role"] == role


async def test_demo_login_disabled_when_demo_mode_off(client: AsyncClient) -> None:
    settings = get_settings()
    settings.demo_mode = False
    try:
        r = await client.post("/api/auth/demo/student")
        assert r.status_code == 404
        r = await client.get("/api/config")
        assert r.json()["demo_mode"] is False
    finally:
        settings.demo_mode = True


async def test_quick_login_enters_the_stand_accounts_but_not_admin(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    """QUICK_LOGIN without the demo mode: the buttons enter the stand's teacher and student."""
    created: list[str] = []
    async with admin_engine.begin() as conn:
        for login_, role in (("teacher", "teacher"), ("student", "student")):
            row = await conn.execute(
                text(
                    "INSERT INTO users (id, login, password_hash, full_name, role, service_code) "
                    "VALUES (gen_random_uuid(), :login, 'x', 'Стенд', :role, :service) "
                    "ON CONFLICT (login) DO NOTHING RETURNING login"
                ),
                {"login": login_, "role": role, "service": "gkh" if role == "student" else None},
            )
            created += [r[0] for r in row]
    settings = get_settings()
    settings.demo_mode = False
    try:
        r = await client.get("/api/config")
        assert r.json()["quick_login_roles"] == []
        assert (await client.post("/api/auth/demo/teacher")).status_code == 404

        settings.quick_login = True
        r = await client.get("/api/config")
        assert sorted(r.json()["quick_login_roles"]) == ["student", "teacher"]
        for role in ("teacher", "student"):
            r = await client.post(f"/api/auth/demo/{role}")
            assert r.status_code == 200, r.text
            assert r.json()["user"]["login"] == role
        assert (await client.post("/api/auth/demo/admin")).status_code == 404
    finally:
        settings.demo_mode = True
        settings.quick_login = False
        if created:
            async with admin_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM users WHERE login = ANY(:logins)"), {"logins": created}
                )


async def test_the_stand_shows_and_enters_the_second_trainee(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    """The stand showing its credentials (20.10.2026): student2 is listed and has a button of
    his own, for the video «преподаватель + 2 обучаемых»; no other login can be slipped in."""
    created: list[str] = []
    async with admin_engine.begin() as conn:
        for login_, role in (
            ("teacher", "teacher"),
            ("student", "student"),
            ("student2", "student"),
            ("admin", "admin"),
        ):
            row = await conn.execute(
                text(
                    "INSERT INTO users (id, login, password_hash, full_name, role, service_code) "
                    "VALUES (gen_random_uuid(), :login, 'x', 'Стенд', :role, :service) "
                    "ON CONFLICT (login) DO NOTHING RETURNING login"
                ),
                {"login": login_, "role": role, "service": "gkh" if role == "student" else None},
            )
            created += [r[0] for r in row]
    settings = get_settings()
    settings.demo_mode = False
    settings.quick_login = True
    settings.show_demo_credentials = True
    try:
        shown = (await client.get("/api/config")).json()["demo_credentials"]
        assert ("student", "student2") in {(c["role"], c["login"]) for c in shown}
        r = await client.post("/api/auth/demo/student", params={"login": "student2"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["login"] == "student2"
        r = await client.post("/api/auth/demo/student", params={"login": "teacher"})
        assert r.status_code == 404
        r = await client.post("/api/auth/demo/teacher", params={"login": "student2"})
        assert r.status_code == 404
    finally:
        settings.demo_mode = True
        settings.quick_login = False
        settings.show_demo_credentials = False
        if created:
            async with admin_engine.begin() as conn:
                await conn.execute(
                    text("DELETE FROM users WHERE login = ANY(:logins)"), {"logins": created}
                )


async def test_shown_credentials_are_listed_and_locked(client: AsyncClient) -> None:
    """SHOW_DEMO_CREDENTIALS: the login page gets login and password by role; nobody can
    change a password that everybody sees."""
    r = await client.get("/api/config")
    assert r.json()["demo_credentials"] == []
    settings = get_settings()
    settings.show_demo_credentials = True
    try:
        shown = (await client.get("/api/config")).json()["demo_credentials"]
        assert {(c["role"], c["login"]) for c in shown} == {
            ("student", "student1"),
            ("teacher", "teacher1"),
            ("admin", "admin"),
        }
        assert {c["password"] for c in shown} == {SEED_PASSWORD}
        r = await client.post(
            "/api/auth/login", json={"login": "teacher1", "password": SEED_PASSWORD}
        )
        assert r.status_code == 200
        r = await client.post(
            "/api/auth/change-password",
            json={"old_password": SEED_PASSWORD, "new_password": "Other-Pass-33"},
            headers=bearer(r.json()),
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "shown_password"
    finally:
        settings.show_demo_credentials = False


async def test_validation_error_shape(client: AsyncClient) -> None:
    r = await client.post("/api/auth/login", json={"login": "student1"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
    assert "password" in r.json()["error"]["message"]


async def test_health_and_config(client: AsyncClient) -> None:
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["postgres"] == "ok"
    r = await client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["demo_mode"] is True
