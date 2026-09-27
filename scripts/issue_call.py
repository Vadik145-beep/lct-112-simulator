"""Issues a call-intake attempt to a trainee on a running stand (wave 6, «Проверка»): the
backend then rings the trainee's softphone. Until wave 7 the teacher's form cannot create a
call-intake lesson, so the script makes (or reuses) one directly in the database.

    uv run --project backend python scripts/issue_call.py --student student1 \\
        --scenario call_2-1_zadymlenie_musoroprovoda

Database and Redis are those of the stand; the addresses come from .env with the container
host names replaced by localhost and the published ports (POSTGRES_PORT, REDIS_PORT).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

SESSION_KEY = "stand-call-intake"


def read_env() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if not path.exists():
        return values
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def stand_urls(env: dict[str, str]) -> tuple[str, str]:
    """Database and Redis of the stand as seen from the host."""
    db = env.get("DATABASE_ADMIN_URL", "postgresql+asyncpg://trainer:trainer@postgres:5432/trainer")
    db = db.replace("@postgres:5432", f"@localhost:{env.get('POSTGRES_PORT', '5432')}")
    redis = f"redis://localhost:{env.get('REDIS_PORT', '6379')}/0"
    return db, redis


def configure_app(env: dict[str, str]) -> None:
    db, redis = stand_urls(env)
    os.environ.setdefault("DATABASE_URL", db)
    os.environ.setdefault("DATABASE_ADMIN_URL", db)
    os.environ.setdefault("REDIS_URL", redis)
    os.environ.setdefault("SECRET_KEY", env.get("SECRET_KEY", "x" * 32))
    os.environ.setdefault("DATA_DIR", str(ROOT / "data"))
    os.environ.setdefault("MODELS_DIR", env.get("MODELS_DIR", str(ROOT / "models")))
    os.environ.setdefault("STORAGE_DIR", str(ROOT / "storage"))
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    for name in ("LLM_DIALOG_URL", "LLM_GEN_URL", "STT_URL"):
        os.environ.setdefault(name, "")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--student", default="student1", help="логин обучающегося")
    parser.add_argument("--scenario", default="call_2-1_zadymlenie_musoroprovoda")
    parser.add_argument(
        "--dialog-mode", default="select", choices=["select", "hybrid", "generate", "buttons"]
    )
    parser.add_argument("--norm", type=int, default=90, help="норматив, секунд")
    parser.add_argument(
        "--close-open", action="store_true", help="закрыть незавершённые вызовы обучающегося"
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="только завести занятие (со звонком на телефон), вызов не выдавать",
    )
    return parser.parse_args()


async def issue(args: argparse.Namespace) -> uuid.UUID:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.events import append_event, publish_events
    from app.models import (
        ACTIVE_ATTEMPT_STATES,
        ATTEMPT_FINISHED,
        ATTEMPT_ISSUED,
        MODE_CALL_INTAKE,
        SESSION_RUNNING,
        Attempt,
        Group,
        Scenario,
        TrainingSession,
        User,
    )
    from app.training.service import utcnow

    async with SessionLocal() as session:
        student = await session.scalar(select(User).where(User.login == args.student))
        teacher = await session.scalar(select(User).where(User.login == "teacher1"))
        group = await session.scalar(select(Group).where(Group.title == "Учебная-1"))
        scenario = await session.scalar(select(Scenario).where(Scenario.seed_key == args.scenario))
        if student is None or teacher is None or group is None:
            raise SystemExit("нет демо-пользователей: запустите python -m app.seed на стенде")
        if scenario is None:
            raise SystemExit(f"сценарий {args.scenario} не найден в базе")
        ts = await session.scalar(
            select(TrainingSession).where(TrainingSession.seed_key == SESSION_KEY)
        )
        if ts is None:
            ts = TrainingSession(
                seed_key=SESSION_KEY,
                title="Приём вызова: проверка телефонии",
                teacher_id=teacher.id,
                group_id=group.id,
                mode=MODE_CALL_INTAKE,
                scenario_ids=[scenario.id],
                norm_seconds=args.norm,
                dialog_mode=args.dialog_mode,
                voice_enabled=True,
                # Calls go through Asterisk only in a lesson with this box (27.09.2026).
                phone_calls=True,
                status=SESSION_RUNNING,
                started_at=utcnow(),
            )
            session.add(ts)
        else:
            ts.status = SESSION_RUNNING
            ts.phone_calls = True
            ts.dialog_mode = args.dialog_mode
            ts.norm_seconds = args.norm
            if scenario.id not in ts.scenario_ids:
                ts.scenario_ids = [*ts.scenario_ids, scenario.id]
        await session.flush()
        if args.close_open:
            open_attempts = await session.scalars(
                select(Attempt).where(
                    Attempt.session_id == ts.id,
                    Attempt.student_id == student.id,
                    Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
                )
            )
            for old in open_attempts:
                old.state = ATTEMPT_FINISHED
                old.submitted_at = utcnow()
        if args.prepare:
            await session.commit()
            return ts.id
        existing = list(await session.scalars(select(Attempt.id).where(Attempt.session_id == ts.id)))
        attempt = Attempt(
            session_id=ts.id,
            student_id=student.id,
            scenario_id=scenario.id,
            scenario_version=scenario.current_version,
            mode=MODE_CALL_INTAKE,
            card_number=str(9000 + len(existing) + 1),
            issued_at=utcnow(),
            state=ATTEMPT_ISSUED,
        )
        session.add(attempt)
        await session.flush()
        event = await append_event(
            session,
            session_id=ts.id,
            type_="attempt.issued",
            student_id=student.id,
            payload={
                "attempt_id": attempt.id,
                "card_number": attempt.card_number,
                "state": attempt.state,
                "scenario_id": scenario.id,
                "issued_at": attempt.issued_at,
            },
        )
        await session.commit()
        await publish_events([event])
        return attempt.id


def main() -> None:
    args = parse_args()
    configure_app(read_env())
    attempt_id = asyncio.run(issue(args))
    print(f"попытка {attempt_id} выдана: {args.student}, сценарий {args.scenario}")


if __name__ == "__main__":
    main()
