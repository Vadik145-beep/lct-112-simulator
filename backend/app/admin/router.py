"""Administrator API (PRD 11): users, services, state of the system, notifications, audit
log, backups and settings. Every endpoint requires the administrator role; the
administrator has no way to touch evaluations, scenarios or a running lesson (those
endpoints answer 403, see tests/api/test_admin.py)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.admin import audit as audit_log
from app.admin import backups as backup_service
from app.admin import health
from app.admin import settings as admin_settings
from app.admin import users as user_service
from app.admin.schemas import (
    AdminServiceOut,
    AdminServicePatch,
    AdminSettingsOut,
    AdminSettingsPatch,
    AdminUserCreated,
    AdminUserIn,
    AdminUserOut,
    AdminUserPatch,
    AuditPage,
    AuditVerifyOut,
    BackupOut,
    BackupsOut,
    HealthOut,
    NotificationOut,
    ResetPasswordOut,
    RevealOut,
    SipAccountAdminOut,
)
from app.audit import write_audit
from app.auth.deps import DbSession, client_ip, require_role
from app.config import get_settings
from app.errors import ApiError
from app.models import Role, Service, User
from app.telephony import service as telephony
from app.telephony import settings as telephony_settings
from app.telephony import sip

router = APIRouter(prefix="/admin", tags=["admin"])

Admin = Annotated[User, Depends(require_role(Role.admin))]


async def _audit(session: DbSession, user: User, request: Request, action: str, **kw) -> None:
    await write_audit(
        session, action=action, actor_id=user.id, actor_role=user.role, ip=client_ip(request), **kw
    )


# ---------------------------------------------------------------- users


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(
    user: Admin, session: DbSession, role: Role | None = None, q: str | None = None
) -> list[AdminUserOut]:
    """Every account with initials instead of the name (see /reveal)."""
    return [
        user_service.user_out(u) for u in await user_service.list_users(session, role=role, query=q)
    ]


@router.post("/users", response_model=AdminUserCreated, status_code=201)
async def create_user(
    body: AdminUserIn, user: Admin, session: DbSession, request: Request
) -> AdminUserCreated:
    created, password = await user_service.create_user(session, body)
    await _audit(
        session,
        user,
        request,
        "user.create",
        entity="user",
        entity_id=str(created.id),
        details={
            "login": created.login,
            "role": created.role.value,
            "service_code": created.service_code,
        },
    )
    await session.commit()
    return AdminUserCreated(user=user_service.user_out(created), temporary_password=password)


@router.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID, body: AdminUserPatch, user: Admin, session: DbSession, request: Request
) -> AdminUserOut:
    target = await user_service.get_user(session, user_id)
    changes = await user_service.update_user(session, target, body, user)
    if changes:
        await _audit(
            session,
            user,
            request,
            "user.update",
            entity="user",
            entity_id=str(target.id),
            details=changes,
        )
        await session.commit()
    return user_service.user_out(target)


@router.post("/users/{user_id}/reveal", response_model=RevealOut)
async def reveal_user(
    user_id: uuid.UUID, user: Admin, session: DbSession, request: Request
) -> RevealOut:
    """«Показать»: the full name; the look is written to the audit log (PRD 3)."""
    target = await user_service.get_user(session, user_id)
    await _audit(session, user, request, "user.reveal", entity="user", entity_id=str(target.id))
    await session.commit()
    return RevealOut(id=target.id, full_name=target.full_name)


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordOut)
async def reset_password(
    user_id: uuid.UUID, user: Admin, session: DbSession, request: Request
) -> ResetPasswordOut:
    """A new temporary password, shown once; the user changes it at the next login."""
    target = await user_service.get_user(session, user_id)
    password = await user_service.reset_password(session, target)
    await _audit(
        session, user, request, "user.reset_password", entity="user", entity_id=str(target.id)
    )
    await session.commit()
    return ResetPasswordOut(id=target.id, temporary_password=password)


@router.post("/users/{user_id}/sip", response_model=SipAccountAdminOut)
async def sip_account(
    user_id: uuid.UUID, user: Admin, session: DbSession, request: Request
) -> SipAccountAdminOut:
    """The softphone account of a trainee (created on first request), for a desk phone or
    for checking the registration."""
    target = await user_service.get_user(session, user_id)
    if target.role != Role.student:
        raise ApiError(409, "not_student", "SIP-учётка есть только у обучающихся.")
    account, created = await sip.ensure_account(session, target)
    config = await telephony_settings.load(session)
    await _audit(
        session,
        user,
        request,
        "user.sip_account",
        entity="user",
        entity_id=str(target.id),
        details={"created": created},
    )
    await session.commit()
    service = telephony.get_service()
    if created and service is not None:
        await service.sync_endpoints()
    s = get_settings()
    return SipAccountAdminOut(
        id=target.id,
        login=account.phone_endpoint,
        password=account.password,
        domain=config.sip_domain,
        ws_path=s.sip_ws_path,
        telephony_enabled=s.telephony_enabled,
    )


# ---------------------------------------------------------------- services


def _service_out(s: Service) -> AdminServiceOut:
    return AdminServiceOut(
        code=s.code,
        title=s.title,
        short_title=s.short_title,
        no_reject=s.no_reject,
        via_arm112=s.via_arm112,
        order=s.order,
    )


@router.get("/services", response_model=list[AdminServiceOut])
async def list_services(user: Admin, session: DbSession) -> list[AdminServiceOut]:
    rows = await session.scalars(select(Service).order_by(Service.order, Service.code))
    return [_service_out(s) for s in rows]


@router.patch("/services/{code}", response_model=AdminServiceOut)
async def update_service(
    code: str, body: AdminServicePatch, user: Admin, session: DbSession, request: Request
) -> AdminServiceOut:
    """Whether the service is alerted through АРМ-112 and whether it may refuse a card."""
    service = await session.get(Service, code)
    if service is None:
        raise ApiError(404, "service_not_found", "Служба не найдена.")
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(service, field, value)
    if changes:
        await _audit(
            session,
            user,
            request,
            "service.update",
            entity="service",
            entity_id=code,
            details=changes,
        )
        await session.commit()
    return _service_out(service)


# ---------------------------------------------------------------- health


@router.get("/health", response_model=HealthOut)
async def system_health(user: Admin, session: DbSession) -> HealthOut:
    """Tiles of the services, load, running lessons and calls, open notifications."""
    return await health.snapshot(session)


def _notification_out(n) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        kind=n.kind,
        source=n.source,
        title=n.title,
        message=n.message,
        at=n.at,
        acknowledged_at=n.acknowledged_at,
    )


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(user: Admin, session: DbSession) -> list[NotificationOut]:
    return [_notification_out(n) for n in await health.open_notifications(session)]


@router.post("/notifications/{notification_id}/ack", response_model=NotificationOut)
async def acknowledge_notification(
    notification_id: uuid.UUID, user: Admin, session: DbSession, request: Request
) -> NotificationOut:
    row = await health.acknowledge(session, notification_id, user.id)
    if row is None:
        raise ApiError(404, "notification_not_found", "Оповещение не найдено.")
    await _audit(
        session,
        user,
        request,
        "notification.ack",
        entity="admin_notification",
        entity_id=str(row.id),
        details={"source": row.source},
    )
    await session.commit()
    return _notification_out(row)


# ---------------------------------------------------------------- audit


@router.get("/audit", response_model=AuditPage)
async def audit_page(
    user: Admin,
    session: DbSession,
    actor_id: uuid.UUID | None = None,
    actor_login: str | None = None,
    action: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query()] = 50,
) -> AuditPage:
    if per_page not in audit_log.PAGE_SIZES:
        raise ApiError(422, "bad_page_size", f"Записей на странице: {audit_log.PAGE_SIZES}.")
    return await audit_log.page(
        session,
        actor_id=actor_id,
        actor_login=actor_login,
        action=action,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )


@router.post("/audit/verify", response_model=AuditVerifyOut)
async def audit_verify(user: Admin, session: DbSession, request: Request) -> AuditVerifyOut:
    """Recomputes the hash chain; the check itself is written to the log afterwards."""
    result = await audit_log.verify(session)
    await _audit(
        session,
        user,
        request,
        "audit.verify",
        entity="audit_log",
        details={"ok": result.ok, "checked": result.checked, "broken_id": result.broken_id},
    )
    await session.commit()
    return result


# ---------------------------------------------------------------- backups


@router.get("/backups", response_model=BackupsOut)
async def list_backups(user: Admin, session: DbSession) -> BackupsOut:
    items = await backup_service.list_backups(session)
    schedule = await admin_settings.load_backups(session)
    await session.commit()
    return BackupsOut(
        folder=str(backup_service.backup_dir()),
        schedule_time=schedule.time,
        keep=schedule.keep,
        service_alive=health._backup_alive(),
        items=items,
    )


@router.post("/backups", response_model=BackupOut, status_code=202)
async def create_backup(user: Admin, session: DbSession, request: Request) -> BackupOut:
    """«Сделать копию сейчас»: the backup service picks the request up within seconds."""
    row = await backup_service.request_backup(session, user.id)
    await _audit(session, user, request, "backup.request", entity="backup", entity_id=str(row.id))
    await session.commit()
    return BackupOut(
        id=row.id,
        kind=row.kind,
        status=row.status,
        file_name=None,
        size_bytes=None,
        requested_at=row.requested_at,
        finished_at=None,
        error=None,
    )


# ---------------------------------------------------------------- settings


async def _settings_out(session: DbSession) -> AdminSettingsOut:
    return AdminSettingsOut(
        telephony=await telephony_settings.load(session),
        logging=await admin_settings.load_logging(session),
        backups=await admin_settings.load_backups(session),
        training=await admin_settings.load_training(session),
    )


@router.get("/settings", response_model=AdminSettingsOut)
async def get_settings_(user: Admin, session: DbSession) -> AdminSettingsOut:
    return await _settings_out(session)


@router.patch("/settings", response_model=AdminSettingsOut)
async def patch_settings(
    body: AdminSettingsPatch, user: Admin, session: DbSession, request: Request
) -> AdminSettingsOut:
    """Telephony (ring timeout, recording, codecs; the ARI address applies after a restart of
    the API), log level (at once), backup schedule (the backup service reads it within a
    minute) and the default «Не завершено» threshold of new lessons."""
    changed: dict = {}
    try:
        if body.telephony is not None:
            await telephony_settings.store(session, body.telephony, user.id)
            changed["telephony"] = body.telephony.model_dump(exclude_none=True)
        if body.logging is not None:
            await admin_settings.store_logging(session, body.logging, user.id)
            changed["logging"] = body.logging.model_dump(exclude_none=True)
        if body.backups is not None:
            await admin_settings.store_backups(session, body.backups, user.id)
            changed["backups"] = body.backups.model_dump(exclude_none=True)
        if body.training is not None:
            await admin_settings.store_training(session, body.training, user.id)
            changed["training"] = body.training.model_dump(exclude_none=True)
    except ValueError as exc:
        raise ApiError(422, "bad_settings", str(exc)) from exc
    if changed:
        await _audit(session, user, request, "settings.update", entity="settings", details=changed)
        await session.commit()
        if "telephony" in changed:
            service = telephony.get_service()
            if service is not None:
                await service.sync_endpoints()
    return await _settings_out(session)
