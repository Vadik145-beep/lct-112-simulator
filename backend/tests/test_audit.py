import pytest
from asyncpg.exceptions import InsufficientPrivilegeError
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.audit import GENESIS_HASH, verify_chain
from app.db import SessionLocal
from tests.conftest import bearer, login


async def _actions(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT action FROM audit_log ORDER BY id"))
        return [r[0] for r in rows]


async def test_login_and_logout_are_audited_in_order(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    await client.post("/api/auth/login", json={"login": "student1", "password": "bad"})
    data = await login(client, "student1")
    await client.post("/api/auth/logout", headers=bearer(data))

    assert await _actions(admin_engine) == ["login_failed", "login", "logout"]

    async with admin_engine.connect() as conn:
        rows = (
            await conn.execute(
                text("SELECT actor_id, actor_role, prev_hash, hash FROM audit_log ORDER BY id")
            )
        ).all()
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[1].prev_hash == rows[0].hash
    assert rows[2].prev_hash == rows[1].hash
    assert rows[1].actor_role == "student" and rows[1].actor_id is not None


async def test_chain_verifies_and_detects_tampering(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    for _ in range(3):
        await login(client, "teacher1")
    async with SessionLocal() as session:
        assert await verify_chain(session) == (True, None)

    # Only the owner role can change a row; doing so breaks the chain at that row.
    async with admin_engine.begin() as conn:
        await conn.execute(text("UPDATE audit_log SET action = 'logout' WHERE id = 2"))
    async with SessionLocal() as session:
        assert await verify_chain(session) == (False, 2)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'x' WHERE id = 1",
        "DELETE FROM audit_log WHERE id = 1",
        "TRUNCATE audit_log",
    ],
)
async def test_app_role_cannot_modify_audit_log(client: AsyncClient, statement: str) -> None:
    await login(client, "admin")
    async with SessionLocal() as session:
        with pytest.raises(DBAPIError) as exc_info:
            await session.execute(text(statement))
        assert isinstance(exc_info.value.orig.__cause__, InsufficientPrivilegeError)
