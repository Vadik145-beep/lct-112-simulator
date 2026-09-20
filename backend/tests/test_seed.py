"""Seed on a clean install (DEMO_MODE=false): one administrator who must change the password,
no demo users, groups, sessions or history. Demo mode keeps creating everything."""

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


async def test_seed_users_creates_only_admin_on_clean_install(
    clean_install, monkeypatch: pytest.MonkeyPatch, admin_engine: AsyncEngine
) -> None:
    suffix = uuid.uuid4().hex[:8]
    specs = [
        {"login": f"admin-{suffix}", "full_name": "Администратор", "role": Role.admin},
        {"login": f"teacher-{suffix}", "full_name": "Преподаватель", "role": Role.teacher},
        {"login": f"student-{suffix}", "full_name": "Обучающийся", "role": Role.student},
    ]
    monkeypatch.setattr(seed_module, "demo_users", lambda: specs)
    try:
        async with SessionLocal() as session:
            created = await seed_module.seed_users(session)
            await session.commit()
        assert created == 1
        async with SessionLocal() as session:
            users = list(await session.scalars(select(User).where(User.login.like(f"%-{suffix}"))))
        assert [u.login for u in users] == [f"admin-{suffix}"]
        assert users[0].role == Role.admin
        assert users[0].must_change_password is True
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
