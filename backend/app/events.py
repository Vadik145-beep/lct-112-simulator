"""Per-session event log (PRD section 12).

Every change in a training session is appended to ``session_events`` with a sequence number
that is dense per session, and published to Redis so WebSocket connections on any backend
replica forward it. Clients reconnect with ``after_seq`` and read what they missed from the
table, so the log, not Redis, is the source of truth.
"""

from __future__ import annotations

import json
import uuid
import zlib
from datetime import UTC, datetime

import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging import get_logger
from app.models import SessionEvent

log = get_logger(__name__)

_redis: aioredis.Redis | None = None


def channel_for(session_id: uuid.UUID) -> str:
    return f"session-events:{session_id}"


def get_redis() -> aioredis.Redis:
    global _redis  # one shared client per process
    if _redis is None:
        _redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def event_to_dict(event: SessionEvent) -> dict:
    return {
        "seq": event.seq,
        "type": event.type,
        "session_id": str(event.session_id),
        "student_id": str(event.student_id) if event.student_id else None,
        "payload": event.payload,
        "at": event.at.astimezone(UTC).isoformat(),
    }


async def append_event(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    type_: str,
    payload: dict,
    student_id: uuid.UUID | None = None,
) -> SessionEvent:
    """Appends an event inside the caller's transaction. The sequence is serialised with a
    per-session advisory lock so two replicas never take the same number."""
    lock_key = zlib.crc32(session_id.bytes)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    last = await session.scalar(
        select(func.max(SessionEvent.seq)).where(SessionEvent.session_id == session_id)
    )
    payload = json.loads(json.dumps(payload, default=_json_default, ensure_ascii=False))
    event = SessionEvent(
        session_id=session_id,
        seq=(last or 0) + 1,
        type=type_,
        student_id=student_id,
        payload=payload,
        at=datetime.now(UTC),
    )
    session.add(event)
    await session.flush()
    return event


async def publish_events(events: list[SessionEvent]) -> None:
    """Pushes committed events to Redis. Failures are logged, never raised: the log is already
    stored and clients catch up through ``after_seq``."""
    if not events:
        return
    try:
        client = get_redis()
        async with client.pipeline(transaction=False) as pipe:
            for event in events:
                pipe.publish(channel_for(event.session_id), json.dumps(event_to_dict(event)))
            await pipe.execute()
    except (OSError, aioredis.RedisError) as exc:
        log.warning("event publish failed", error=str(exc), count=len(events))


async def events_after(
    session: AsyncSession,
    session_id: uuid.UUID,
    after_seq: int,
    *,
    student_id: uuid.UUID | None,
) -> list[SessionEvent]:
    """Events of a session after ``after_seq``; students see shared events and their own."""
    query = (
        select(SessionEvent)
        .where(SessionEvent.session_id == session_id, SessionEvent.seq > after_seq)
        .order_by(SessionEvent.seq)
    )
    if student_id is not None:
        query = query.where(
            (SessionEvent.student_id.is_(None)) | (SessionEvent.student_id == student_id)
        )
    return list(await session.scalars(query))
