"""Load rehearsal for the teacher's live monitoring: a group of mock trainees who act on
their cards about once a second each, so the monitoring page of the session can be watched
for lag and for catching up after a dropped connection.

Run inside the stack:
    docker compose exec backend python -m app.load_monitor --students 20 --seconds 90
Then open the printed session in the teacher's cabinet (teacher1). Everything the script
creates is marked with the «load-» prefix; rerunning reuses the users and the group.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import uuid

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.events import publish_events
from app.logging import configure_logging, get_logger
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    MODE_CARD_RESPONSE,
    Attempt,
    Group,
    Role,
    TrainingSession,
    User,
)
from app.security import hash_password
from app.training import service as training
from app.training import sessions as lessons
from app.training.service import utcnow

log = get_logger(__name__)

LOGIN_PREFIX = "load"
GROUP_TITLE = "Нагрузочная (мок)"
SERVICE = "territorial_oiv"
# Chain a mock trainee walks through after «Принята».
CHAIN = ["response_started", "arrived", "works_started", "works_done"]


async def ensure_users(count: int) -> tuple[list[User], uuid.UUID]:
    settings = get_settings()
    async with SessionLocal() as session:
        users = []
        for i in range(1, count + 1):
            login = f"{LOGIN_PREFIX}{i:02d}"
            user = await session.scalar(select(User).where(User.login == login))
            if user is None:
                user = User(
                    login=login,
                    full_name=f"Мок Обучающийся {i}",
                    role=Role.student,
                    service_code=SERVICE,
                    password_hash=hash_password(settings.seed_password),
                    must_change_password=False,
                )
                session.add(user)
            users.append(user)
        await session.flush()
        teacher = await session.scalar(select(User).where(User.login == "teacher1"))
        group = await session.scalar(select(Group).where(Group.title == GROUP_TITLE))
        if group is None:
            group = Group(title=GROUP_TITLE, teacher_id=teacher.id)
            session.add(group)
            await session.flush()
        await lessons.set_members(session, group, users)
        ts = TrainingSession(
            title=f"Нагрузка мониторинга: {count} обучающихся, {utcnow():%H:%M}",
            teacher_id=teacher.id,
            group_id=group.id,
            mode=MODE_CARD_RESPONSE,
            difficulty=1,
            service_profile=[SERVICE],
            norm_seconds=30,
            pass_threshold=70,
        )
        session.add(ts)
        await session.flush()
        events = await lessons.start_session(session, ts, teacher)
        await session.commit()
        await publish_events(events)
        ids = [u.id for u in users]
        session_id = ts.id
    async with SessionLocal() as session:
        return list(await session.scalars(select(User).where(User.id.in_(ids)))), session_id


async def act(session_id: uuid.UUID, student_id: uuid.UUID) -> int:
    """One action of one trainee; returns the number of events published."""
    async with SessionLocal() as session:
        ts = await session.get(TrainingSession, session_id)
        student = await session.get(User, student_id)
        active = list(
            await session.scalars(
                select(Attempt).where(
                    Attempt.session_id == session_id,
                    Attempt.student_id == student_id,
                    Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
                )
            )
        )
        events = []
        if not active:
            _, events = await training.issue_cards(session, ts, student)
        else:
            attempt = active[0]
            if attempt.state == "issued":
                events = await training.open_attempt(session, attempt, ts)
            elif attempt.response_status == "received":
                # Sometimes hesitate past the norm so «Не оповещено» shows up on the tiles.
                if random.random() < 0.15:  # noqa: S311 - a rehearsal, not security
                    return 0
                change = await training.set_status(
                    session,
                    attempt,
                    ts,
                    student,
                    status="accepted",
                    order_number=None,
                    comment=None,
                    reject_reason=None,
                    action_id=None,
                )
                events = change.events
            else:
                done = [e["status"] for e in attempt.status_log]
                nxt = next((s for s in CHAIN if s not in done), None)
                if nxt is None:
                    change = await training.finish_attempt(session, attempt, ts, student)
                else:
                    change = await training.set_status(
                        session,
                        attempt,
                        ts,
                        student,
                        status=nxt,
                        order_number="14-217" if nxt == "response_started" else None,
                        comment="Работы выполнены, пострадавших нет"
                        if nxt == "works_done"
                        else None,
                        reject_reason=None,
                        action_id=None,
                    )
                events = change.events
        await session.commit()
    await publish_events(events)
    return len(events)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--students", type=int, default=20)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--keep", action="store_true", help="не завершать занятие в конце")
    args = parser.parse_args()
    users, session_id = await ensure_users(args.students)
    print(f"Занятие: /teacher/sessions/{session_id}  (teacher1)", flush=True)
    total = 0
    for tick in range(args.seconds):
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(*(act(session_id, u.id) for u in users))
        total += sum(results)
        spent = asyncio.get_running_loop().time() - started
        print(
            f"[{tick + 1:3d} с] событий {sum(results):3d}, всего {total}, {spent * 1000:.0f} мс",
            flush=True,
        )
        await asyncio.sleep(max(0.0, 1.0 - spent))
    if not args.keep:
        async with SessionLocal() as session:
            ts = await session.get(TrainingSession, session_id)
            teacher = await session.get(User, ts.teacher_id)
            events = await lessons.finish_session(session, ts, teacher)
            await session.commit()
        await publish_events(events)
        print("Занятие завершено.", flush=True)


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(main())
