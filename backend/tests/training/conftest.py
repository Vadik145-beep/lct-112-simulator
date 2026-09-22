"""Timeline tests are pure: no PostgreSQL, no Redis. The session-wide database fixtures of the
top-level conftest are replaced with no-ops here."""

import pytest


@pytest.fixture(scope="session", autouse=True)
async def migrated_database() -> None:
    return None


@pytest.fixture(autouse=True)
async def reset_state() -> None:
    return None
