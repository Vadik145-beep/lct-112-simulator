"""Request and response models of the administrator API (PRD 11, 13.7)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.admin.settings import (
    BackupSettings,
    BackupSettingsPatch,
    LoggingSettings,
    LoggingSettingsPatch,
    TrainingSettings,
    TrainingSettingsPatch,
)
from app.models import Role
from app.telephony.settings import TelephonySettings, TelephonySettingsPatch

# ---------------------------------------------------------------- users


class AdminUserOut(BaseModel):
    id: uuid.UUID
    login: str
    # Initials until «Показать» (PRD 3: the administrator sees a name only through an
    # audited action); the full name comes from /reveal.
    display_name: str
    role: Role
    service_code: str | None
    is_blocked: bool
    must_change_password: bool
    has_sip_account: bool
    created_at: datetime
    last_login_at: datetime | None


class AdminUserIn(BaseModel):
    login: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field(min_length=1, max_length=200)
    role: Role
    service_code: str | None = Field(default=None, max_length=32)
    # Empty = a temporary password is generated and returned once.
    password: str | None = Field(default=None, min_length=8, max_length=256)


class AdminUserPatch(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    service_code: str | None = Field(default=None, max_length=32)
    # Explicit flag so an empty service can be set: {"service_code": null, "clear_service": true}.
    clear_service: bool = False
    is_blocked: bool | None = None


class AdminUserCreated(BaseModel):
    user: AdminUserOut
    # Shown once; the user must change it at the first login.
    temporary_password: str


class RevealOut(BaseModel):
    id: uuid.UUID
    full_name: str


class ResetPasswordOut(BaseModel):
    id: uuid.UUID
    temporary_password: str


class SipAccountAdminOut(BaseModel):
    id: uuid.UUID
    login: str
    password: str
    domain: str
    ws_path: str
    telephony_enabled: bool


# ---------------------------------------------------------------- services


class AdminServiceOut(BaseModel):
    code: str
    title: str
    short_title: str
    no_reject: bool
    via_arm112: bool
    order: int


class AdminServicePatch(BaseModel):
    no_reject: bool | None = None
    via_arm112: bool | None = None


# ---------------------------------------------------------------- health


class ServiceTile(BaseModel):
    name: str
    title: str
    # ok | down | off (not configured / profile not started)
    status: str
    detail: str | None = None
    latency_ms: float | None = None


class SystemLoad(BaseModel):
    cpu_percent: float | None
    cpu_count: int | None
    memory_total_mb: int | None
    memory_used_mb: int | None
    load_1: float | None


class HealthOut(BaseModel):
    checked_at: datetime
    services: list[ServiceTile]
    load: SystemLoad
    running_sessions: int
    active_calls: int
    active_attempts: int
    open_notifications: int
    version: str


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    source: str
    title: str
    message: str
    at: datetime
    acknowledged_at: datetime | None


# ---------------------------------------------------------------- audit


class AuditRow(BaseModel):
    id: int
    at: datetime
    actor_id: uuid.UUID | None
    actor_login: str | None
    actor_role: str | None
    action: str
    entity: str | None
    entity_id: str | None
    details: dict | None
    ip: str | None


class AuditPage(BaseModel):
    items: list[AuditRow]
    page: int
    per_page: int
    total: int
    actions: list[str]


class AuditVerifyOut(BaseModel):
    ok: bool
    checked: int
    broken_id: int | None
    message: str


# ---------------------------------------------------------------- backups


class BackupOut(BaseModel):
    id: uuid.UUID | None
    kind: str
    status: str
    file_name: str | None
    size_bytes: int | None
    requested_at: datetime
    finished_at: datetime | None
    error: str | None


class BackupsOut(BaseModel):
    folder: str
    schedule_time: str
    keep: int
    service_alive: bool
    items: list[BackupOut]


# ---------------------------------------------------------------- settings


class AdminSettingsOut(BaseModel):
    telephony: TelephonySettings
    logging: LoggingSettings
    backups: BackupSettings
    training: TrainingSettings


class AdminSettingsPatch(BaseModel):
    telephony: TelephonySettingsPatch | None = None
    logging: LoggingSettingsPatch | None = None
    backups: BackupSettingsPatch | None = None
    training: TrainingSettingsPatch | None = None
