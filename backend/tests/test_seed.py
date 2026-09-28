"""Seed on a clean install (DEMO_MODE=false): an administrator who must change the password
and the stand's ``teacher`` and ``student``; no demo users, groups, sessions or history. Demo
mode keeps creating everything."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app import seed as seed_module
from app.config import get_settings
from app.db import SessionLocal
from app.models import Role, User


@pytest.fixture
def clean_install():
    settings = get_settings()
    settings.demo_mode = False
    try:
        yield settings
    finally:
        settings.demo_mode = True


async def test_seed_users_creates_the_stand_accounts_on_clean_install(
    clean_install, monkeypatch: pytest.MonkeyPatch, admin_engine: AsyncEngine
) -> None:
    suffix = uuid.uuid4().hex[:8]
    specs = [
        {"login": f"admin-{suffix}", "full_name": "Администратор", "role": Role.admin},
        {"login": f"teacher-{suffix}", "full_name": "Преподаватель", "role": Role.teacher},
        {"login": f"student-{suffix}", "full_name": "Обучающийся", "role": Role.student},
    ]
    demo = [*specs, {"login": f"teacher2-{suffix}", "full_name": "Демо", "role": Role.teacher}]
    monkeypatch.setattr(seed_module, "demo_users", lambda: demo)
    monkeypatch.setattr(seed_module, "clean_install_users", lambda: specs)
    try:
        async with SessionLocal() as session:
            created = await seed_module.seed_users(session)
            await session.commit()
        assert created == 3
        async with SessionLocal() as session:
            users = {
                u.login: u
                for u in await session.scalars(select(User).where(User.login.like(f"%-{suffix}")))
            }
        assert set(users) == {f"admin-{suffix}", f"teacher-{suffix}", f"student-{suffix}"}
        assert users[f"admin-{suffix}"].must_change_password is True
        assert users[f"teacher-{suffix}"].must_change_password is False
        assert users[f"student-{suffix}"].must_change_password is False
    finally:
        async with admin_engine.begin() as conn:
            await conn.execute(text("DELETE FROM users WHERE login LIKE :p"), {"p": f"%-{suffix}"})


async def test_seed_skips_demo_content_on_clean_install(
    clean_install, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def spy(name):
        async def _inner(*args, **kwargs):
            calls.append(name)
            return 0 if name == "history" else False

        return _inner

    monkeypatch.setattr(seed_module, "seed_groups", await spy("groups"))
    monkeypatch.setattr(seed_module, "seed_demo_session", await spy("session"))
    monkeypatch.setattr(seed_module, "seed_demo_call_session", await spy("call_session"))
    monkeypatch.setattr(seed_module, "seed_history", await spy("history"))

    await seed_module.seed()
    assert calls == []

    clean_install.demo_mode = True
    await seed_module.seed()
    assert calls == ["groups", "session", "call_session", "history"]
