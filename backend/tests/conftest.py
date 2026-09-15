"""Test fixtures: a dedicated PostgreSQL database (TEST_DATABASE_*_URL), migrated with
Alembic once per session and reset between tests with the owner connection."""

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Minimal .env reader so tests pick up the same passwords as docker compose."""
    for candidate in (BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

_DEFAULT_ADMIN = "postgresql+asyncpg://trainer:trainer-dev-password@localhost:5432/trainer_test"
_DEFAULT_APP = (
    "postgresql+asyncpg://trainer_app:trainer-app-dev-password@localhost:5432/trainer_test"
)
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", _DEFAULT_ADMIN)
APP_URL = os.environ.get("TEST_DATABASE_URL", _DEFAULT_APP)

# The application reads settings at import time, so point it at the test database first.
os.environ.update(
    {
        "APP_ENV": "test",
        "DEMO_MODE": "true",
        "COOKIE_SECURE": "false",
        "DATABASE_URL": APP_URL,
        "DATABASE_ADMIN_URL": ADMIN_URL,
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1"),
        "SECRET_KEY": os.environ.get("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789"),
        "LOG_LEVEL": "WARNING",
    }
)

from app.config import get_settings  # noqa: E402
from app.seed import seed  # noqa: E402

SEED_PASSWORD = get_settings().seed_password


async def _ensure_database() -> None:
    """Creates the test database if missing (connects to the maintenance database)."""
    db_name = ADMIN_URL.rsplit("/", 1)[1]
    app_user = get_settings().app_db_user
    maintenance = create_async_engine(
        ADMIN_URL.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT"
    )
    async with maintenance.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": db_name}
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        await conn.execute(text(f'GRANT CONNECT ON DATABASE "{db_name}" TO "{app_user}"'))
    await maintenance.dispose()
    owner = create_async_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    async with owner.connect() as conn:
        await conn.execute(text(f'GRANT USAGE ON SCHEMA public TO "{app_user}"'))
    await owner.dispose()


@pytest.fixture(scope="session", autouse=True)
async def migrated_database() -> AsyncIterator[None]:
    await _ensure_database()
    env = {**os.environ, "DATABASE_ADMIN_URL": ADMIN_URL}
    for direction in (("downgrade", "base"), ("upgrade", "head")):
        subprocess.run(  # noqa: S603, ASYNC221 - one-off setup, blocking is fine
            [sys.executable, "-m", "alembic", *direction], cwd=BACKEND_DIR, env=env, check=True
        )
    await seed()
    yield


@pytest.fixture(scope="session")
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(ADMIN_URL)
    yield engine
    await engine.dispose()


@pytest.fixture(autouse=True)
async def reset_state(admin_engine: AsyncEngine) -> None:
    """Every test starts with an empty audit log and pristine demo users."""
    from app.security import hash_password

    async with admin_engine.begin() as conn:
        await conn.execute(text("TRUNCATE audit_log RESTART IDENTITY"))
        await conn.execute(
            text(
                "UPDATE users SET failed_attempts = 0, locked_until = NULL, token_version = 0, "
                "must_change_password = false, is_blocked = false, password_hash = :h"
            ),
            {"h": hash_password(SEED_PASSWORD)},
        )


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def login(client: AsyncClient, login: str, password: str = SEED_PASSWORD) -> dict:
    r = await client.post("/api/auth/login", json={"login": login, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def bearer(token: dict | str) -> dict[str, str]:
    value = token["access_token"] if isinstance(token, dict) else token
    return {"Authorization": f"Bearer {value}"}
