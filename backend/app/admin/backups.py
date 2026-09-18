"""Backups for the administrator (PRD 14): the list of dumps in the shared folder and
«Сделать копию сейчас».

``pg_dump`` lives in the ``backup`` container, not in the API. A manual copy is a request
file in ``<backup_dir>/requests/<id>.request``; the backup service (deploy/backup/backup.sh)
polls the folder, writes the dump and answers with ``<id>.done`` (the file name) or
``<id>.failed`` (the error). The API reads the answers when the list is requested.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import BackupOut
from app.config import get_settings
from app.errors import ApiError
from app.logging import get_logger
from app.models import (
    BACKUP_DONE,
    BACKUP_FAILED,
    BACKUP_MANUAL,
    BACKUP_REQUESTED,
    BACKUP_RUNNING,
    BACKUP_SCHEDULED,
    Backup,
)

log = get_logger(__name__)

REQUESTS_DIR = "requests"
DUMP_PATTERN = re.compile(r"^trainer-(\d{8}-\d{6})(-manual)?\.dump$")
PENDING = (BACKUP_REQUESTED, BACKUP_RUNNING)


def backup_dir() -> Path:
    return Path(get_settings().backup_dir)


def _requests_dir() -> Path:
    return backup_dir() / REQUESTS_DIR


def _dump_time(name: str) -> datetime | None:
    match = DUMP_PATTERN.match(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def list_files() -> dict[str, tuple[int, datetime]]:
    """Dump files of the folder: name → (size, modified)."""
    files: dict[str, tuple[int, datetime]] = {}
    try:
        for path in backup_dir().iterdir():
            if path.is_file() and DUMP_PATTERN.match(path.name):
                stat = path.stat()
                files[path.name] = (stat.st_size, datetime.fromtimestamp(stat.st_mtime, UTC))
    except OSError:
        pass
    return files


async def request_backup(session: AsyncSession, actor_id: uuid.UUID | None) -> Backup:
    """Writes the request file for the backup service and records the row."""
    row = Backup(kind=BACKUP_MANUAL, status=BACKUP_REQUESTED, requested_by=actor_id)
    session.add(row)
    await session.flush()
    folder = _requests_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{row.id}.request").write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    except OSError as exc:
        log.warning("backup request not written", folder=str(folder), error=str(exc))
        raise ApiError(
            503,
            "backups_unavailable",
            "Папка резервных копий недоступна: проверьте, что служба backup запущена и "
            "том backups смонтирован в контейнер API.",
        ) from exc
    return row


async def sync_pending(session: AsyncSession) -> None:
    """Reads the answers of the backup service for the requests still pending."""
    pending = list(await session.scalars(select(Backup).where(Backup.status.in_(PENDING))))
    if not pending:
        return
    folder = _requests_dir()
    for row in pending:
        done = folder / f"{row.id}.done"
        failed = folder / f"{row.id}.failed"
        running = folder / f"{row.id}.running"
        try:
            if done.is_file():
                name = done.read_text(encoding="utf-8").strip()
                row.file_name = name
                size = (backup_dir() / name).stat().st_size if name else None
                row.size_bytes = size
                row.status = BACKUP_DONE
                row.finished_at = datetime.now(UTC)
            elif failed.is_file():
                row.error = failed.read_text(encoding="utf-8").strip()[:2000] or "ошибка pg_dump"
                row.status = BACKUP_FAILED
                row.finished_at = datetime.now(UTC)
            elif running.is_file():
                row.status = BACKUP_RUNNING
        except OSError as exc:
            log.warning("backup answer unreadable", id=str(row.id), error=str(exc))
    await session.flush()


async def list_backups(session: AsyncSession) -> list[BackupOut]:
    """Rows of the requests plus the scheduled dumps that exist only as files."""
    await sync_pending(session)
    rows = list(await session.scalars(select(Backup).order_by(Backup.requested_at.desc())))
    files = list_files()
    items: list[BackupOut] = []
    known: set[str] = set()
    for row in rows:
        if row.file_name:
            known.add(row.file_name)
            present = row.file_name in files
            size = files[row.file_name][0] if present else row.size_bytes
        else:
            present, size = False, row.size_bytes
        status = row.status
        if status == BACKUP_DONE and not present:
            status = "removed"  # rotated out by the keep limit
        items.append(
            BackupOut(
                id=row.id,
                kind=row.kind,
                status=status,
                file_name=row.file_name,
                size_bytes=size,
                requested_at=row.requested_at,
                finished_at=row.finished_at,
                error=row.error,
            )
        )
    for name, (size, modified) in files.items():
        if name in known:
            continue
        items.append(
            BackupOut(
                id=None,
                kind=BACKUP_MANUAL if name.endswith("-manual.dump") else BACKUP_SCHEDULED,
                status=BACKUP_DONE,
                file_name=name,
                size_bytes=size,
                requested_at=_dump_time(name) or modified,
                finished_at=modified,
                error=None,
            )
        )
    items.sort(key=lambda b: b.requested_at, reverse=True)
    return items
