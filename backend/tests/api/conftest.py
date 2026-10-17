"""Training API tests need the reference tables and the seed scenarios."""

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.importers import organizers
from app.models import TrainingSession, User
from app.seed import seed
from app.training import service as training
from tests.conftest import BACKEND_DIR

DATA_DIR = BACKEND_DIR.parent / "data"


async def issue_now(session_id: uuid.UUID | str, login: str, cards: int = 3) -> None:
    """Puts the first ``cards`` of the queue into the trainee's journal at once. At difficulty 3
    the cards come as a stream on a timer (app.training.stream); the tests of the journal, the
    monitor and the report take them here instead of waiting for it."""
    async with SessionLocal() as session:
        ts = await session.get(TrainingSession, uuid.UUID(str(session_id)))
        student = await session.scalar(select(User).where(User.login == login))
        assert ts is not None and student is not None
        await training.issue_next(session, ts, student, cards)
        await session.commit()


@pytest.fixture(scope="package", autouse=True)
async def dataset() -> None:
    """Reference tables from the dataset, then the seed again so scenarios link to tickets."""
    await organizers.run(DATA_DIR)
    await seed(DATA_DIR)


# Since 18.10.2026 a progress status needs a comment (memo p. 31, example 6); tests that do
# not care what the dispatcher wrote get this one.
PROGRESS_COMMENT = "Доложено бригадой"


def status_body(body: dict) -> dict:
    """The body of a status request with a comment where the status requires one."""
    if body.get("status") in ("response_started", "arrived", "works_started") and not body.get(
        "comment"
    ):
        return {**body, "comment": PROGRESS_COMMENT}
    return body
