"""Background check that marks cards «Не оповещено» when the primary status is late.

Runs inside the API process every ``SWEEP_INTERVAL_SECONDS``; the update is row-locked with
``SKIP LOCKED`` so several replicas may run it at once without duplicate events.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.db import SessionLocal
from app.events import publish_events
from app.logging import get_logger
from app.training.service import sweep_not_notified

log = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 5


async def sweep_once() -> int:
    async with SessionLocal() as session:
        events = await sweep_not_notified(session)
        await session.commit()
    await publish_events(events)
    return len(events)


async def run_forever() -> None:
    while True:
        try:
            await sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # keep the loop alive, report and retry
            log.warning("card status sweep failed", error=str(exc))
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


def start() -> asyncio.Task:
    return asyncio.create_task(run_forever(), name="card-status-sweep")


async def stop(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
