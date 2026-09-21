"""Creates the initial data. Idempotent: existing users keep their passwords and flags,
scenarios get a new version only when their file changed, the demo session keeps its status.

``DEMO_MODE=true`` (the jury stand, development): demo users, groups, two running sessions
and a month of history (PRD section 15). ``DEMO_MODE=false`` (a clean install at the
customer's): only the scenarios from the organizers' tickets and one ``admin`` who must
change the password at the first login; teachers, trainees and groups are created by hand.

Run: python -m app.seed
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.logging import configure_logging, get_logger
from app.models import (
    MODE_CALL_INTAKE,
    MODE_CARD_RESPONSE,
    SCENARIO_APPROVED,
    SESSION_RUNNING,
    Group,
    GroupMember,
    Role,
    Scenario,
    ScenarioVersion,
    Ticket,
    TrainingSession,
    User,
)
from app.security import hash_password
from app.seed_history import seed_history
from app.training.service import utcnow

log = get_logger(__name__)

# Service profiles are assigned to students so wave 3 can route cards by service.
# Codes are those of the ``services`` table (see app.importers.classifier.SERVICE_COLUMNS).
_STUDENT_SERVICES = ["territorial_oiv", "gkh", "gormost", "mosvodostok", "mosgaz", "moslift"]

# Groups (PRD section 15): «Учебная-1» = student1-6 with teacher1, «Учебная-2» = student7-12.
_GROUPS = [
    {"title": "Учебная-1", "teacher": "teacher1", "students": range(1, 7)},
    {"title": "Учебная-2", "teacher": "teacher2", "students": range(7, 13)},
]

SCENARIOS_DIR = "seed/scenarios"
DEMO_SESSION_KEY = "demo-card-response-1"
DEMO_CALL_SESSION_KEY = "demo-call-intake-1"
# student1 works as a district administration dispatcher (see _STUDENT_SERVICES).
DEMO_SERVICE_PROFILE = ["territorial_oiv"]


def demo_users() -> list[dict]:
    users = [
        {"login": "admin", "full_name": "Администратор системы", "role": Role.admin},
        {"login": "teacher1", "full_name": "Иванова Мария Петровна", "role": Role.teacher},
        {"login": "teacher2", "full_name": "Сидоров Алексей Николаевич", "role": Role.teacher},
    ]
    surnames = [
        "Кузнецов",
        "Смирнова",
        "Попов",
        "Васильева",
        "Петров",
        "Соколова",
        "Михайлов",
        "Новикова",
        "Фёдоров",
        "Морозова",
        "Волков",
        "Алексеева",
    ]
    for i, surname in enumerate(surnames, start=1):
        users.append(
            {
                "login": f"student{i}",
                "full_name": f"{surname} Обучающийся {i}",
                "role": Role.student,
                "service_code": _STUDENT_SERVICES[(i - 1) % len(_STUDENT_SERVICES)],
            }
        )
    return users


async def seed_users(session: AsyncSession) -> int:
    """All demo users in demo mode; only the administrator on a clean install."""
    settings = get_settings()
    password_hash = hash_password(settings.seed_password)
    existing = set(await session.scalars(select(User.login)))
    specs = demo_users() if settings.demo_mode else demo_users()[:1]
    created = 0
    for spec in specs:
        if spec["login"] in existing:
            continue
        session.add(
            User(password_hash=password_hash, must_change_password=not settings.demo_mode, **spec)
        )
        created += 1
    await session.flush()
    return created


async def seed_groups(session: AsyncSession) -> int:
    users = {u.login: u for u in await session.scalars(select(User))}
    created = 0
    for spec in _GROUPS:
        group = await session.scalar(select(Group).where(Group.title == spec["title"]))
        if group is None:
            group = Group(title=spec["title"], teacher_id=users[spec["teacher"]].id)
            session.add(group)
            await session.flush()
            created += 1
        members = set(
            await session.scalars(
                select(GroupMember.student_id).where(GroupMember.group_id == group.id)
            )
        )
        for i in spec["students"]:
            student = users.get(f"student{i}")
            if student is not None and student.id not in members:
                session.add(GroupMember(group_id=group.id, student_id=student.id))
    await session.flush()
    return created


def _scenario_files(data_dir: Path) -> list[Path]:
    folder = data_dir / SCENARIOS_DIR
    return sorted(folder.glob("*.json")) if folder.exists() else []


async def _ticket_id(session: AsyncSession, ticket_ref: str | None) -> uuid.UUID | None:
    if not ticket_ref or "-" not in ticket_ref:
        return None
    ticket_no, item_no = ticket_ref.split("-", 1)
    if not (ticket_no.isdigit() and item_no.isdigit()):
        return None
    return await session.scalar(
        select(Ticket.id).where(Ticket.ticket_no == int(ticket_no), Ticket.item_no == int(item_no))
    )


async def seed_scenarios(session: AsyncSession, data_dir: Path) -> tuple[int, int, list[str]]:
    """Loads ``data/seed/scenarios/*.json`` (PRD 9.2 / 9.3 bodies). The file stem is the
    seed key; a changed body becomes a new version. Returns (created, updated, keys)."""
    created = updated = 0
    keys: list[str] = []
    for path in _scenario_files(data_dir):
        body = json.loads(path.read_text(encoding="utf-8"))
        key = path.stem
        keys.append(key)
        scenario = await session.scalar(select(Scenario).where(Scenario.seed_key == key))
        fields = {
            "kind": body.get("kind", MODE_CARD_RESPONSE),
            "title": body.get("title", key),
            "ticket_ref": body.get("ticket_ref"),
            "ticket_id": await _ticket_id(session, body.get("ticket_ref")),
            "incident_type_code": (body.get("card") or body.get("reference_card") or {}).get(
                "incident_type"
            ),
            "service_code": body.get("service"),
            "difficulty": int(body.get("difficulty", 1)),
            # Seed files are the reviewed reference scenarios (PRD 15) unless they say otherwise.
            "status": body.get("status", SCENARIO_APPROVED),
        }
        if scenario is None:
            scenario = Scenario(seed_key=key, source="ticket", current_version=1, **fields)
            session.add(scenario)
            await session.flush()
            session.add(ScenarioVersion(scenario_id=scenario.id, version=1, body=body))
            created += 1
            continue
        current = await session.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == scenario.id,
                ScenarioVersion.version == scenario.current_version,
            )
        )
        for name, value in fields.items():
            setattr(scenario, name, value)
        if current is None or current.body != body:
            scenario.current_version += 1
            session.add(
                ScenarioVersion(
                    scenario_id=scenario.id,
                    version=scenario.current_version,
                    body=body,
                    revision_comment=f"Обновлено из {path.name}",
                )
            )
            updated += 1
    await session.flush()
    return created, updated, keys


async def seed_demo_session(session: AsyncSession, keys: list[str]) -> bool:
    """One running card-response session for «Учебная-1» (student1 works as управа). The
    queue holds only scenarios whose seed files still exist."""
    teacher = await session.scalar(select(User).where(User.login == "teacher1"))
    group = await session.scalar(select(Group).where(Group.title == "Учебная-1"))
    # Cards of the trainee's service, easy ones first; the duplicate card (difficulty 3)
    # therefore comes after the original it repeats.
    scenarios = sorted(
        await session.scalars(
            select(Scenario).where(
                Scenario.kind == MODE_CARD_RESPONSE,
                Scenario.seed_key.in_(keys),
                Scenario.service_code.in_(DEMO_SERVICE_PROFILE),
            )
        ),
        key=lambda s: (s.difficulty, s.seed_key or ""),
    )
    if teacher is None or group is None or not scenarios:
        return False
    demo = await session.scalar(
        select(TrainingSession).where(TrainingSession.seed_key == DEMO_SESSION_KEY)
    )
    scenario_ids = [s.id for s in scenarios]
    if demo is not None:
        demo.scenario_ids = scenario_ids
        await session.flush()
        return False
    session.add(
        TrainingSession(
            seed_key=DEMO_SESSION_KEY,
            title="Реагирование на карточку: тренировка ДДС управы",
            teacher_id=teacher.id,
            group_id=group.id,
            mode=MODE_CARD_RESPONSE,
            card_source="scenarios",
            scenario_ids=scenario_ids,
            difficulty=3,
            service_profile=DEMO_SERVICE_PROFILE,
            norm_seconds=30,
            pass_threshold=70,
            hints_enabled=True,
            status=SESSION_RUNNING,
            started_at=utcnow(),
        )
    )
    await session.flush()
    return True


async def seed_demo_call_session(session: AsyncSession, keys: list[str]) -> bool:
    """One running call-intake session for «Учебная-1»: the 112 operator takes the seeded
    calls one by one, easy ones first (PRD 9.3; the other-region and dropped-call calls
    come last)."""
    teacher = await session.scalar(select(User).where(User.login == "teacher1"))
    group = await session.scalar(select(Group).where(Group.title == "Учебная-1"))
    scenarios = sorted(
        await session.scalars(
            select(Scenario).where(Scenario.kind == MODE_CALL_INTAKE, Scenario.seed_key.in_(keys))
        ),
        key=lambda s: (s.difficulty, s.seed_key or ""),
    )
    if teacher is None or group is None or not scenarios:
        return False
    demo = await session.scalar(
        select(TrainingSession).where(TrainingSession.seed_key == DEMO_CALL_SESSION_KEY)
    )
    scenario_ids = [s.id for s in scenarios]
    if demo is not None:
        demo.scenario_ids = scenario_ids
        await session.flush()
        return False
    session.add(
        TrainingSession(
            seed_key=DEMO_CALL_SESSION_KEY,
            title="Приём вызова: тренировка операторов 112",
            teacher_id=teacher.id,
            group_id=group.id,
            mode=MODE_CALL_INTAKE,
            card_source="scenarios",
            scenario_ids=scenario_ids,
            difficulty=3,
            norm_seconds=60,
            pass_threshold=70,
            hints_enabled=True,
            voice_enabled=True,
            dialog_mode="select",
            status=SESSION_RUNNING,
            started_at=utcnow(),
        )
    )
    await session.flush()
    return True


async def seed(data_dir: Path | None = None) -> int:
    settings = get_settings()
    data_dir = data_dir or Path(settings.data_dir)
    async with SessionLocal() as session:
        users_created = await seed_users(session)
        scenarios_created, scenarios_updated, keys = await seed_scenarios(session, data_dir)
        groups_created = 0
        session_created = call_session_created = False
        history_attempts = 0
        if settings.demo_mode:
            groups_created = await seed_groups(session)
            session_created = await seed_demo_session(session, keys)
            call_session_created = await seed_demo_call_session(session, keys)
            history_attempts = await seed_history(session)
        await session.commit()
    log.info(
        "seed finished",
        demo_mode=settings.demo_mode,
        users_created=users_created,
        groups_created=groups_created,
        scenarios_created=scenarios_created,
        scenarios_updated=scenarios_updated,
        demo_session_created=session_created,
        demo_call_session_created=call_session_created,
        history_attempts=history_attempts,
    )
    return users_created


if __name__ == "__main__":
    configure_logging(get_settings().log_level)
    asyncio.run(seed())
