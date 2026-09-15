"""Append-only audit log with a SHA-256 hash chain.

Every row stores the hash of the previous row and its own hash over the canonical
JSON of its fields. Inserts are serialised with a transaction-level advisory lock so
the chain never forks. The application database role has no UPDATE/DELETE on the table
(see migration 0001), so tampering requires the owner role and breaks the chain.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

GENESIS_HASH = "0" * 64
# Arbitrary constant key for pg_advisory_xact_lock; only has to be unique within the app.
_AUDIT_LOCK_KEY = 112_001


def _canonical(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def compute_hash(
    *,
    at: datetime,
    actor_id: uuid.UUID | None,
    actor_role: str | None,
    action: str,
    entity: str | None,
    entity_id: str | None,
    details: dict | None,
    ip: str | None,
    prev_hash: str,
) -> str:
    payload = {
        "at": at.astimezone(UTC).isoformat(timespec="microseconds"),
        "actor_id": str(actor_id) if actor_id else None,
        "actor_role": actor_role,
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "details": details,
        "ip": ip,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


async def write_audit(
    session: AsyncSession,
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    entity: str | None = None,
    entity_id: str | None = None,
    details: dict | None = None,
    ip: str | None = None,
) -> AuditLog:
    """Appends a row inside the caller's transaction (caller commits)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_LOCK_KEY})
    last = await session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))
    prev_hash = last or GENESIS_HASH
    at = datetime.now(UTC)
    row = AuditLog(
        at=at,
        actor_id=actor_id,
        actor_role=actor_role,
        action=action,
        entity=entity,
        entity_id=entity_id,
        details=details,
        ip=ip,
        prev_hash=prev_hash,
        hash=compute_hash(
            at=at,
            actor_id=actor_id,
            actor_role=actor_role,
            action=action,
            entity=entity,
            entity_id=entity_id,
            details=details,
            ip=ip,
            prev_hash=prev_hash,
        ),
    )
    session.add(row)
    await session.flush()
    return row


async def verify_chain(session: AsyncSession) -> tuple[bool, int | None]:
    """Recomputes every hash. Returns (ok, id of the first broken row or None)."""
    prev = GENESIS_HASH
    result = await session.stream_scalars(select(AuditLog).order_by(AuditLog.id))
    async for row in result:
        expected = compute_hash(
            at=row.at,
            actor_id=row.actor_id,
            actor_role=row.actor_role,
            action=row.action,
            entity=row.entity,
            entity_id=row.entity_id,
            details=row.details,
            ip=row.ip,
            prev_hash=prev,
        )
        if row.prev_hash != prev or row.hash != expected:
            return False, row.id
        prev = row.hash
    return True, None
