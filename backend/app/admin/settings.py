"""Operational settings of the administrator (PRD 10 ``settings``, 13.7): logging,
backups and the training defaults. The telephony section lives in ``app.telephony.settings``;
this module stores the other sections the same way (one JSON document per key) and applies
the ones that take effect at runtime.

The backup schedule is handed to the ``backup`` service through a file in the shared backups
folder (``.schedule``), which the service reads on every loop; no restart is needed.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.logging import configure_logging, get_logger
from app.models import UNFINISHED_SECONDS_DEFAULT, Setting

log = get_logger(__name__)

LOGGING_KEY = "logging"
BACKUPS_KEY = "backups"
TRAINING_KEY = "training"
SCHEDULE_FILE = ".schedule"

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LoggingSettings(BaseModel):
    level: LogLevel


class LoggingSettingsPatch(BaseModel):
    level: LogLevel | None = None


class BackupSettings(BaseModel):
    # Daily time «HH:MM» in the stand's TZ and how many dumps to keep.
    time: str
    keep: int = Field(ge=1, le=365)


class BackupSettingsPatch(BaseModel):
    time: str | None = None
    keep: int | None = Field(default=None, ge=1, le=365)

    @field_validator("time")
    @classmethod
    def _check_time(cls, value: str | None) -> str | None:
        if value is not None and not _TIME_RE.match(value):
            raise ValueError("Время копии задаётся как ЧЧ:ММ, например 03:00.")
        return value


class TrainingSettings(BaseModel):
    # Default seconds before an open card becomes «Не завершено» in a new lesson.
    unfinished_seconds: int = Field(ge=60, le=14 * 24 * 3600)


class TrainingSettingsPatch(BaseModel):
    unfinished_seconds: int | None = Field(default=None, ge=60, le=14 * 24 * 3600)


def logging_defaults() -> LoggingSettings:
    level = get_settings().log_level.upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level = "INFO"
    return LoggingSettings(level=level)  # type: ignore[arg-type]


def backup_defaults() -> BackupSettings:
    s = get_settings()
    return BackupSettings(time=s.backup_time, keep=s.backup_keep)


def training_defaults() -> TrainingSettings:
    return TrainingSettings(unfinished_seconds=UNFINISHED_SECONDS_DEFAULT)


def _merge(model: type[BaseModel], base: BaseModel, stored: dict | None) -> BaseModel:
    data = base.model_dump()
    for key, value in (stored or {}).items():
        if key in data and value is not None:
            data[key] = value
    return model.model_validate(data)


async def _load_row(session: AsyncSession, key: str) -> dict | None:
    row = await session.get(Setting, key)
    return row.value if row else None


async def load_logging(session: AsyncSession) -> LoggingSettings:
    return _merge(LoggingSettings, logging_defaults(), await _load_row(session, LOGGING_KEY))  # type: ignore[return-value]


async def load_backups(session: AsyncSession) -> BackupSettings:
    return _merge(BackupSettings, backup_defaults(), await _load_row(session, BACKUPS_KEY))  # type: ignore[return-value]


async def load_training(session: AsyncSession) -> TrainingSettings:
    return _merge(TrainingSettings, training_defaults(), await _load_row(session, TRAINING_KEY))  # type: ignore[return-value]


async def _store(
    session: AsyncSession, key: str, changes: dict, actor_id: uuid.UUID | None
) -> dict:
    row = await session.get(Setting, key)
    value = dict(row.value) if row else {}
    value.update(changes)
    if row is None:
        row = Setting(key=key, value=value)
        session.add(row)
    else:
        row.value = value
    row.updated_by = actor_id
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return value


async def store_logging(
    session: AsyncSession, patch: LoggingSettingsPatch, actor_id: uuid.UUID | None
) -> LoggingSettings:
    value = await _store(session, LOGGING_KEY, patch.model_dump(exclude_none=True), actor_id)
    merged = _merge(LoggingSettings, logging_defaults(), value)
    apply_logging(merged)  # type: ignore[arg-type]
    return merged  # type: ignore[return-value]


def apply_logging(settings: LoggingSettings) -> None:
    """Changes the level of the running process (the worker picks it up at its next start)."""
    configure_logging(settings.level)
    log.info("log level changed", level=settings.level)


async def store_backups(
    session: AsyncSession, patch: BackupSettingsPatch, actor_id: uuid.UUID | None
) -> BackupSettings:
    value = await _store(session, BACKUPS_KEY, patch.model_dump(exclude_none=True), actor_id)
    merged = _merge(BackupSettings, backup_defaults(), value)
    write_schedule(merged)  # type: ignore[arg-type]
    return merged  # type: ignore[return-value]


def backup_dir() -> Path:
    return Path(get_settings().backup_dir)


def write_schedule(settings: BackupSettings) -> bool:
    """Hands the schedule to the backup service; False when the folder is not mounted."""
    folder = backup_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / SCHEDULE_FILE).write_text(
            f"BACKUP_TIME={settings.time}\nBACKUP_KEEP={settings.keep}\n", encoding="utf-8"
        )
        return True
    except OSError as exc:
        log.warning("backup schedule not written", folder=str(folder), error=str(exc))
        return False


async def store_training(
    session: AsyncSession, patch: TrainingSettingsPatch, actor_id: uuid.UUID | None
) -> TrainingSettings:
    value = await _store(session, TRAINING_KEY, patch.model_dump(exclude_none=True), actor_id)
    return _merge(TrainingSettings, training_defaults(), value)  # type: ignore[return-value]


async def apply_stored(session: AsyncSession) -> None:
    """At startup: the stored log level and backup schedule take effect."""
    logging_settings = await load_logging(session)
    if logging_settings != logging_defaults():
        apply_logging(logging_settings)
    write_schedule(await load_backups(session))
