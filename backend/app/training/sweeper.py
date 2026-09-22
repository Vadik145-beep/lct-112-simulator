"""Background checks of the cards in work: «Не оповещено» when the primary status is late,
and the squad's reports to the dispatcher on their timeline (``app.training.reports``).

Runs inside the API process every ``SWEEP_INTERVAL_SECONDS``; the updates are row-locked with
``SKIP LOCKED`` so several replicas may run them at once without duplicate events.
"""

from __future__ import annotations

import asyncio
import contextlib

from app.db import SessionLocal
from app.events import publish_events
from app.logging import get_logger
from app.training.reports import dial_reports, sweep_reports
from app.training.service import sweep_not_notified

log = get_logger(__name__)

SWEEP_INTERVAL_SECONDS = 5


async def sweep_once() -> int:
    async with SessionLocal() as session:
        events = await sweep_not_notified(session)
        report_events, to_dial = await sweep_reports(session)
        events.extend(report_events)
        await session.commit()
    await publish_events(events)
    if to_dial:
        await publish_events(await dial_reports(to_dial))
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
