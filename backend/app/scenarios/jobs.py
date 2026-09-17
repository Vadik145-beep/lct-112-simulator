"""Background jobs of the scenario library (generation, revision, voicing) with progress the
client polls through ``GET /jobs/{id}``.

A job is an ``asyncio`` task inside the API process; its state lives in Redis (``job:<id>``
hash, ``JOB_TTL``) so any backend replica answers the poll and a restart leaves a readable
«failed» trace instead of a job that never finishes. Generation takes seconds (template) to a
couple of minutes (7B model); a queue worker would add a second deployment path for no gain.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.events import get_redis
from app.logging import get_logger

log = get_logger(__name__)

JOB_TTL = timedelta(hours=24)
JOB_KEY = "job:{id}"
STALE_AFTER = timedelta(minutes=15)  # a «running» job older than this is reported as failed

_tasks: set[asyncio.Task] = set()


class JobHandle:
    """Given to the job coroutine to report progress."""

    def __init__(self, job_id: str) -> None:
        self.id = job_id

    async def progress(self, percent: int, message: str | None = None) -> None:
        await _update(
            self.id, status="running", progress=max(0, min(100, percent)), message=message
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _update(job_id: str, **fields: Any) -> None:
    redis = get_redis()
    mapping = {
        k: (
            json.dumps(v, ensure_ascii=False)
            if isinstance(v, dict | list)
            else ("" if v is None else str(v))
        )
        for k, v in fields.items()
    }
    mapping["updated_at"] = _now()
    key = JOB_KEY.format(id=job_id)
    await redis.hset(key, mapping=mapping)
    await redis.expire(key, JOB_TTL)


async def create(job_type: str, owner_id: uuid.UUID, payload: dict | None = None) -> str:
    job_id = uuid.uuid4().hex
    await _update(
        job_id,
        id=job_id,
        type=job_type,
        owner_id=str(owner_id),
        status="queued",
        progress=0,
        message="В очереди",
        result=None,
        error=None,
        payload=payload or {},
        created_at=_now(),
    )
    return job_id


async def get(job_id: str) -> dict[str, Any] | None:
    data = await get_redis().hgetall(JOB_KEY.format(id=job_id))
    if not data:
        return None
    job: dict[str, Any] = dict(data)
    for key in ("result", "payload"):
        job[key] = json.loads(job[key]) if job.get(key) else None
    job["progress"] = int(job.get("progress") or 0)
    for key in ("message", "error"):
        job[key] = job.get(key) or None
    job["created_at"] = datetime.fromisoformat(job["created_at"])
    job["updated_at"] = datetime.fromisoformat(job["updated_at"])
    if (
        job["status"] in {"queued", "running"}
        and datetime.now(UTC) - job["updated_at"] > STALE_AFTER
    ):
        job["status"] = "failed"
        job["error"] = "Задача не завершилась: сервер был перезапущен. Запустите генерацию ещё раз."
    return job


def start(job_id: str, work: Callable[[JobHandle], Awaitable[dict[str, Any] | None]]) -> None:
    """Runs ``work`` in the background; its return value is the job result."""

    async def runner() -> None:
        handle = JobHandle(job_id)
        try:
            await _update(job_id, status="running", progress=1, message="Запускаем")
            result = await work(handle)
            await _update(
                job_id, status="done", progress=100, message="Готово", result=result or {}
            )
        except Exception as exc:  # the job must end in a readable state whatever happened
            log.exception("job failed", job_id=job_id)
            await _update(job_id, status="failed", error=str(exc) or exc.__class__.__name__)

    task = asyncio.create_task(runner(), name=f"job-{job_id}")
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_all(max_seconds: float = 30.0) -> None:
    """Tests and shutdown: let the started jobs finish."""
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=max_seconds)
