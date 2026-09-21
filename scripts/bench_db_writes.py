"""Скорость записи в базу по ТЗ («не менее 100 операций в секунду»).

Пишет теми же путями, что и приложение: события занятия (``events.append_event`` — с
последовательным номером под advisory-lock на занятие) и записи аудита (``audit.write_audit``
— хэш-цепочка под общим advisory-lock), в N параллельных соединениях, каждое — своя
транзакция на запись, как у запроса API. Считает записей в секунду и задержку одной записи.

    DATABASE_URL=postgresql+asyncpg://... uv run --project backend \
        python scripts/bench_db_writes.py --seconds 20 --writers 8 --sessions 20

``--sessions`` — по скольким занятиям раскладывать события (у каждого своя очередь номеров:
20 одновременных занятий по ТЗ). Записи помечены «bench-» и удаляются в конце.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import delete, select, text  # noqa: E402

from app.audit import write_audit  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.events import append_event  # noqa: E402
from app.models import Group, SessionEvent, TrainingSession, User  # noqa: E402

TITLE = "bench-db-writes"


async def prepare(count: int) -> list[uuid.UUID]:
    async with SessionLocal() as session:
        teacher = await session.scalar(select(User).where(User.login == "teacher1"))
        group = await session.scalar(select(Group))
        ids = []
        for i in range(count):
            ts = TrainingSession(
                title=f"{TITLE} {i}",
                teacher_id=teacher.id if teacher else None,
                group_id=group.id if group else None,
                mode="card_response",
                difficulty=1,
                service_profile=[],
                norm_seconds=30,
                pass_threshold=70,
            )
            session.add(ts)
            await session.flush()
            ids.append(ts.id)
        await session.commit()
    return ids


async def cleanup() -> None:
    async with SessionLocal() as session:
        ids = list(
            await session.scalars(
                select(TrainingSession.id).where(TrainingSession.title.like(f"{TITLE}%"))
            )
        )
        if ids:
            await session.execute(delete(SessionEvent).where(SessionEvent.session_id.in_(ids)))
            await session.execute(delete(TrainingSession).where(TrainingSession.id.in_(ids)))
        # The audit chain is append-only by design (REVOKE DELETE for the app role); bench rows stay
        # marked «bench.write» — the verifier treats them like any other action.
        await session.commit()


async def writer(kind: str, session_ids: list[uuid.UUID], deadline: float, out: list[float]) -> int:
    n = 0
    while time.perf_counter() < deadline:
        sid = session_ids[n % len(session_ids)]
        t = time.perf_counter()
        async with SessionLocal() as session:
            if kind == "event":
                await append_event(
                    session,
                    session_id=sid,
                    type_="bench.tick",
                    payload={"n": n, "text": "Работы выполнены, пострадавших нет"},
                )
            else:
                await write_audit(
                    session,
                    action="bench.write",
                    entity="bench",
                    entity_id=str(sid),
                    details={"n": n},
                )
            await session.commit()
        out.append((time.perf_counter() - t) * 1000)
        n += 1
    return n


async def run(kind: str, writers: int, seconds: int, session_ids: list[uuid.UUID]) -> str:
    latencies: list[float] = []
    started = time.perf_counter()
    counts = await asyncio.gather(
        *[
            writer(kind, session_ids[i::writers] or session_ids, started + seconds, latencies)
            for i in range(writers)
        ]
    )
    elapsed = time.perf_counter() - started
    total = sum(counts)
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    return (
        f"| {kind} | {writers} | {total} | {total / elapsed:.0f} "
        f"| {statistics.median(latencies):.1f} | {p95:.1f} | {latencies[-1]:.1f} |"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--seconds", type=int, default=20)
    parser.add_argument("--writers", type=int, default=8)
    parser.add_argument("--sessions", type=int, default=20)
    args = parser.parse_args()

    async with SessionLocal() as session:
        version = await session.scalar(text("select version()"))
    print(f"PostgreSQL: {version.split(',')[0]}")
    await cleanup()
    session_ids = await prepare(args.sessions)
    try:
        rows = [
            "| Запись | Соединений | Записей | Записей/с | p50, мс | p95, мс | max, мс |",
            "|---|---|---|---|---|---|---|",
        ]
        rows.append(await run("event", args.writers, args.seconds, session_ids))
        rows.append(await run("audit", args.writers, args.seconds, session_ids))
        print("\n".join(rows))
    finally:
        await cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
