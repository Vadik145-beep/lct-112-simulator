"""State of the system for the administrator (PRD 13.7): a tile per service, the load of the
machine, the running lessons and calls, and the notifications.

A monitor loop (started with the application) repeats the checks every
``HEALTH_MONITOR_SECONDS`` and opens a notification when a service is down or a configured
AI provider does not answer for two passes in a row (one slow answer on a busy machine is
not an incident); the administrator acknowledges it in the cabinet. One open notification
per source: a service that stays down does not flood the list.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.schemas import HealthOut, ServiceTile, SystemLoad
from app.config import get_settings
from app.db import SessionLocal, engine
from app.logging import get_logger
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    CALL_ANSWERED,
    CALL_RINGING,
    NOTIFY_PROVIDER_UNAVAILABLE,
    NOTIFY_SERVICE_DOWN,
    SESSION_RUNNING,
    AdminNotification,
    Attempt,
    TrainingSession,
)

log = get_logger(__name__)

VERSION = "0.1.0"
CHECK_TIMEOUT_SECONDS = 5.0
# Resolving the name of a compose service whose profile is not started hangs in Docker's DNS
# for seconds: the name is resolved first, with its own short budget.
RESOLVE_TIMEOUT_SECONDS = 1.5
# Passes in a row a service must be down before the monitor opens a notification.
DOWN_PASSES_TO_NOTIFY = 2
# The backup service touches a file in the shared folder every minute; older = not alive.
BACKUP_ALIVE_FILE = ".alive"
BACKUP_ALIVE_MAX_AGE = timedelta(minutes=5)
# ARQ writes its health line under this key while the worker runs.
WORKER_HEALTH_KEY = "arq:queue:health-check"

STATUS_OK = "ok"
STATUS_DOWN = "down"
STATUS_OFF = "off"

# name → (title, kind of the notification when it is down)
SERVICES: dict[str, tuple[str, str]] = {
    "backend": ("API", NOTIFY_SERVICE_DOWN),
    "worker": ("Фоновые задачи", NOTIFY_SERVICE_DOWN),
    "postgres": ("PostgreSQL", NOTIFY_SERVICE_DOWN),
    "redis": ("Redis", NOTIFY_SERVICE_DOWN),
    "languagetool": ("LanguageTool (грамотность)", NOTIFY_PROVIDER_UNAVAILABLE),
    "asterisk": ("Asterisk (телефония)", NOTIFY_SERVICE_DOWN),
    "llm-dialog": ("Модель диалога", NOTIFY_PROVIDER_UNAVAILABLE),
    "llm-gen": ("Модель генерации", NOTIFY_PROVIDER_UNAVAILABLE),
    "stt": ("Распознавание речи", NOTIFY_PROVIDER_UNAVAILABLE),
    "backup": ("Резервные копии", NOTIFY_SERVICE_DOWN),
}


# ---------------------------------------------------------------- checks


class ServiceOffError(Exception):
    """The service is configured but not started on this stand (its name does not resolve)."""


async def _timed(coro) -> tuple[str, str | None, float]:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(coro, timeout=CHECK_TIMEOUT_SECONDS)
        return STATUS_OK, detail, round((time.perf_counter() - started) * 1000, 1)
    except ServiceOffError as exc:
        return STATUS_OFF, str(exc), round((time.perf_counter() - started) * 1000, 1)
    except Exception as exc:  # any failure means the tile is red
        reason = str(exc) or type(exc).__name__
        return STATUS_DOWN, reason[:200], round((time.perf_counter() - started) * 1000, 1)


async def _check_postgres() -> str | None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return None


async def _check_redis() -> str | None:
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=CHECK_TIMEOUT_SECONDS)
    try:
        await client.ping()
    finally:
        await client.aclose()
    return None


async def _check_worker() -> str | None:
    client = Redis.from_url(get_settings().redis_url, socket_connect_timeout=CHECK_TIMEOUT_SECONDS)
    try:
        line = await client.get(WORKER_HEALTH_KEY)
    finally:
        await client.aclose()
    if not line:
        raise RuntimeError("worker не отвечает (нет записи о состоянии)")
    return line.decode() if isinstance(line, bytes) else str(line)


async def _resolve(url: str) -> None:
    host = urlparse(url).hostname or ""
    try:
        await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(host, None), timeout=RESOLVE_TIMEOUT_SECONDS
        )
    except (socket.gaierror, TimeoutError) as exc:
        raise ServiceOffError(f"сервис {host} не запущен (профиль не поднят)") from exc


async def _check_http(url: str, auth: tuple[str, str] | None = None) -> str | None:
    await _resolve(url)
    async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_SECONDS, auth=auth) as client:
        response = await client.get(url)
        response.raise_for_status()
    return None


def _backup_alive() -> bool:
    path = Path(get_settings().backup_dir) / BACKUP_ALIVE_FILE
    try:
        age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return False
    return age <= BACKUP_ALIVE_MAX_AGE


async def _check_backup() -> str | None:
    if not _backup_alive():
        raise RuntimeError("служба копий не отмечалась больше 5 минут")
    return None


async def check_services() -> list[ServiceTile]:
    """Every tile of the «Состояние системы» screen, checked in parallel."""
    s = get_settings()
    checks: dict[str, object] = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "worker": _check_worker(),
        "backup": _check_backup(),
    }
    if s.languagetool_url:
        checks["languagetool"] = _check_http(f"{s.languagetool_url.rstrip('/')}/v2/languages")
    if s.telephony_enabled:
        checks["asterisk"] = _check_http(
            f"{s.ari_url.rstrip('/')}/asterisk/info", auth=(s.ari_user, s.ari_password)
        )
    for name, url in (
        ("llm-dialog", s.llm_dialog_url),
        ("llm-gen", s.llm_gen_url),
        ("stt", s.stt_url),
    ):
        if url:
            checks[name] = _check_http(f"{url.rstrip('/')}/health")
    results = await asyncio.gather(*(_timed(c) for c in checks.values()))
    by_name = dict(zip(checks, results, strict=True))
    tiles = [
        ServiceTile(name="backend", title=SERVICES["backend"][0], status=STATUS_OK, detail=VERSION)
    ]
    for name, (title, _kind) in SERVICES.items():
        if name == "backend":
            continue
        if name in by_name:
            status, detail, latency = by_name[name]
            tiles.append(
                ServiceTile(
                    name=name, title=title, status=status, detail=detail, latency_ms=latency
                )
            )
        else:
            tiles.append(
                ServiceTile(name=name, title=title, status=STATUS_OFF, detail="не настроено")
            )
    return tiles


async def model_availability() -> dict[str, bool]:
    """Which AI services answer right now: for the teacher's lesson form (docs/BUGS.md, 10).
    ``dialog`` — the caller's replies, ``generation`` — scenario generation, ``stt`` —
    speech recognition, ``tts`` — the caller's voice (in-process, no probe)."""
    from app.dialog.service import tts_available

    s = get_settings()
    probes = {
        "dialog": s.llm_dialog_url,
        "generation": s.llm_gen_url,
        "stt": s.stt_url,
    }

    async def alive(url: str | None) -> bool:
        if not url:
            return False
        try:
            await _check_http(f"{url.rstrip('/')}/health")
        except Exception:  # any failure means «not available»
            return False
        return True

    results = await asyncio.gather(*(alive(url) for url in probes.values()))
    out = dict(zip(probes, results, strict=True))
    out["tts"] = tts_available()
    return out


# ---------------------------------------------------------------- load


def _read_cpu_times() -> tuple[int, int] | None:
    try:
        with open("/proc/stat", encoding="ascii") as f:
            fields = f.readline().split()[1:]
    except OSError:
        return None
    values = [int(v) for v in fields]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return idle, sum(values)


def _sample_load() -> SystemLoad:
    """Linux only (the containers): /proc; elsewhere the numbers are empty."""
    cpu_count = os.cpu_count()
    first = _read_cpu_times()
    cpu_percent = None
    if first is not None:
        time.sleep(0.2)
        second = _read_cpu_times()
        if second is not None and second[1] > first[1]:
            idle = second[0] - first[0]
            total = second[1] - first[1]
            cpu_percent = round(100.0 * (1 - idle / total), 1)
    total_mb = used_mb = None
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            info = {line.split(":")[0]: int(line.split()[1]) for line in f if ":" in line}
        total_mb = info["MemTotal"] // 1024
        used_mb = (info["MemTotal"] - info.get("MemAvailable", info.get("MemFree", 0))) // 1024
    except (OSError, KeyError, ValueError):
        pass
    load_1 = None
    try:
        load_1 = round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):
        pass
    return SystemLoad(
        cpu_percent=cpu_percent,
        cpu_count=cpu_count,
        memory_total_mb=total_mb,
        memory_used_mb=used_mb,
        load_1=load_1,
    )


async def system_load() -> SystemLoad:
    return await asyncio.to_thread(_sample_load)


# ---------------------------------------------------------------- notifications


async def open_notifications(session: AsyncSession) -> list[AdminNotification]:
    return list(
        await session.scalars(
            select(AdminNotification)
            .where(AdminNotification.acknowledged_at.is_(None))
            .order_by(AdminNotification.at.desc())
        )
    )


async def notify(
    session: AsyncSession, *, kind: str, source: str, title: str, message: str
) -> AdminNotification | None:
    """Opens a notification unless one for the same source is already open."""
    existing = await session.scalar(
        select(AdminNotification).where(
            AdminNotification.source == source, AdminNotification.acknowledged_at.is_(None)
        )
    )
    if existing is not None:
        return None
    row = AdminNotification(kind=kind, source=source, title=title, message=message)
    session.add(row)
    await session.flush()
    log.warning("admin notification", source=source, title=title)
    return row


async def acknowledge(
    session: AsyncSession, notification_id: uuid.UUID, actor_id: uuid.UUID
) -> AdminNotification | None:
    row = await session.get(AdminNotification, notification_id)
    if row is None:
        return None
    if row.acknowledged_at is None:
        row.acknowledged_at = datetime.now(UTC)
        row.acknowledged_by = actor_id
        await session.flush()
    return row


async def notify_from_tiles(
    session: AsyncSession, tiles: list[ServiceTile], streaks: dict[str, int] | None = None
) -> int:
    """One notification per service that is down (for ``DOWN_PASSES_TO_NOTIFY`` passes when
    ``streaks`` is given). Returns how many were opened."""
    opened = 0
    for tile in tiles:
        if tile.status != STATUS_DOWN:
            if streaks is not None:
                streaks.pop(tile.name, None)
            continue
        if streaks is not None:
            streaks[tile.name] = streaks.get(tile.name, 0) + 1
            if streaks[tile.name] < DOWN_PASSES_TO_NOTIFY:
                continue
        kind = SERVICES.get(tile.name, (tile.title, NOTIFY_SERVICE_DOWN))[1]
        if kind == NOTIFY_PROVIDER_UNAVAILABLE:
            title = f"Недоступен провайдер: {tile.title}"
            message = (
                f"{tile.title} не отвечает ({tile.detail or 'нет ответа'}). Оценка и занятия "
                "продолжают работать без него; проверьте контейнер и перезапустите его."
            )
        else:
            title = f"Падение сервиса: {tile.title}"
            message = (
                f"{tile.title} не отвечает ({tile.detail or 'нет ответа'}). "
                "Проверьте контейнер: docker compose ps, docker compose logs."
            )
        if await notify(session, kind=kind, source=tile.name, title=title, message=message):
            opened += 1
    return opened


# ---------------------------------------------------------------- snapshot


async def counters(session: AsyncSession) -> tuple[int, int, int, int]:
    running = await session.scalar(
        select(func.count())
        .select_from(TrainingSession)
        .where(TrainingSession.status == SESSION_RUNNING)
    )
    calls = await session.scalar(
        select(func.count())
        .select_from(Attempt)
        .where(Attempt.call_state.in_((CALL_RINGING, CALL_ANSWERED)))
    )
    attempts = await session.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.state.in_(ACTIVE_ATTEMPT_STATES))
    )
    open_count = await session.scalar(
        select(func.count())
        .select_from(AdminNotification)
        .where(AdminNotification.acknowledged_at.is_(None))
    )
    return int(running or 0), int(calls or 0), int(attempts or 0), int(open_count or 0)


async def snapshot(session: AsyncSession) -> HealthOut:
    """The screen: fresh tiles; notifications come from the monitor loop only."""
    tiles, load = await asyncio.gather(check_services(), system_load())
    running, calls, attempts, open_count = await counters(session)
    return HealthOut(
        checked_at=datetime.now(UTC),
        services=tiles,
        load=load,
        running_sessions=running,
        active_calls=calls,
        active_attempts=attempts,
        open_notifications=open_count,
        version=VERSION,
    )


# ---------------------------------------------------------------- monitor loop

# Consecutive failed passes per service (the process' own memory; a restart starts over).
_down_streaks: dict[str, int] = {}


async def run_check_once() -> int:
    """One pass of the monitor: checks the services and opens notifications for those down
    for the second pass in a row."""
    tiles = await check_services()
    async with SessionLocal() as session:
        opened = await notify_from_tiles(session, tiles, _down_streaks)
        await session.commit()
    return opened


async def _loop(interval: int) -> None:
    # The first pass waits: right after the start the services are still warming up.
    await asyncio.sleep(interval)
    while True:
        try:
            await run_check_once()
        except Exception as exc:  # the monitor must survive any failure
            log.warning("health monitor failed", error=str(exc))
        await asyncio.sleep(interval)


def start() -> asyncio.Task | None:
    interval = get_settings().health_monitor_seconds
    if interval <= 0:
        return None
    return asyncio.create_task(_loop(interval), name="health-monitor")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
