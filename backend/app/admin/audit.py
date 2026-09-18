"""Audit log for the administrator: filtered pages and the integrity check (PRD 14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import AuditPage, AuditRow, AuditVerifyOut
from app.audit import verify_chain
from app.models import AuditLog, User

PAGE_SIZES = (20, 50, 100)


async def page(
    session: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    actor_login: str | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    per_page: int,
) -> AuditPage:
    query = select(AuditLog)
    if actor_login:
        user = await session.scalar(select(User).where(User.login == actor_login.strip().lower()))
        # An unknown login matches nothing rather than everything.
        actor_id = user.id if user else uuid.UUID(int=0)
    if actor_id is not None:
        query = query.where(AuditLog.actor_id == actor_id)
    if action:
        query = query.where(AuditLog.action == action.strip())
    if date_from is not None:
        query = query.where(AuditLog.at >= date_from)
    if date_to is not None:
        query = query.where(AuditLog.at <= date_to)
    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        await session.scalars(
            query.order_by(AuditLog.id.desc()).offset((page - 1) * per_page).limit(per_page)
        )
    )
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    logins: dict[uuid.UUID, str] = {}
    if actor_ids:
        for user in await session.scalars(select(User).where(User.id.in_(actor_ids))):
            logins[user.id] = user.login
    actions = [
        a
        for (a,) in await session.execute(
            select(AuditLog.action).distinct().order_by(AuditLog.action)
        )
    ]
    return AuditPage(
        items=[
            AuditRow(
                id=r.id,
                at=r.at,
                actor_id=r.actor_id,
                actor_login=logins.get(r.actor_id) if r.actor_id else None,
                actor_role=r.actor_role,
                action=r.action,
                entity=r.entity,
                entity_id=r.entity_id,
                details=r.details,
                ip=r.ip,
            )
            for r in rows
        ],
        page=page,
        per_page=per_page,
        total=int(total),
        actions=actions,
    )


async def verify(session: AsyncSession) -> AuditVerifyOut:
    ok, broken = await verify_chain(session)
    checked = await session.scalar(select(func.count()).select_from(AuditLog)) or 0
    if ok:
        message = f"Цепочка целая: проверено записей — {checked}."
    else:
        message = (
            f"Цепочка нарушена на записи №{broken}: запись или её предшественница изменены "
            "в обход приложения."
        )
    return AuditVerifyOut(ok=ok, checked=int(checked), broken_id=broken, message=message)
