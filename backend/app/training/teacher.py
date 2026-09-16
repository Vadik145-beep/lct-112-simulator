"""Teacher API (PRD 11): groups, sessions with start and finish, live monitoring snapshot
and the session report. Every endpoint requires the teacher role; a colleague's session or
group answers 403."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.audit import write_audit
from app.auth.deps import DbSession, client_ip, require_role
from app.errors import ApiError
from app.events import publish_events
from app.models import (
    SESSION_DRAFT,
    Group,
    Role,
    Scenario,
    TrainingSession,
    User,
)
from app.training import present
from app.training import report as reporting
from app.training import sessions as lessons
from app.training.teacher_schemas import (
    GroupIn,
    GroupOut,
    GroupPatch,
    MonitorOut,
    QueueScenarioOut,
    ReportOut,
    SessionIn,
    SessionListItem,
    SessionOut,
    SessionPatch,
    StudentOut,
)

router = APIRouter(tags=["teacher"])

Teacher = Annotated[User, Depends(require_role(Role.teacher))]


def _student_out(user: User) -> StudentOut:
    return StudentOut(
        id=user.id, login=user.login, full_name=user.full_name, service_code=user.service_code
    )


# ---------------------------------------------------------------- groups


@router.get("/students", response_model=list[StudentOut])
async def list_students(user: Teacher, session: DbSession) -> list[StudentOut]:
    """Every trainee of the system: the pool a group is composed from."""
    rows = await session.scalars(
        select(User)
        .where(User.role == Role.student, User.is_blocked.is_(False))
        .order_by(User.full_name)
    )
    return [_student_out(u) for u in rows]


async def _group_out(session: DbSession, groups: list[Group]) -> list[GroupOut]:
    members = await lessons.group_members(session, [g.id for g in groups])
    return [
        GroupOut(
            id=g.id,
            title=g.title,
            members=[_student_out(u) for u in members.get(g.id, [])],
            created_at=g.created_at,
        )
        for g in groups
    ]


@router.get("/groups", response_model=list[GroupOut])
async def list_groups(user: Teacher, session: DbSession) -> list[GroupOut]:
    return await _group_out(session, await lessons.teacher_groups(session, user))


@router.post("/groups", response_model=GroupOut, status_code=201)
async def create_group(
    body: GroupIn, user: Teacher, session: DbSession, request: Request
) -> GroupOut:
    students = await lessons.check_students(session, body.student_ids)
    group = Group(title=body.title.strip(), teacher_id=user.id)
    session.add(group)
    await session.flush()
    await lessons.set_members(session, group, students)
    await write_audit(
        session,
        action="group.create",
        actor_id=user.id,
        actor_role=user.role,
        entity="group",
        entity_id=str(group.id),
        details={"title": group.title, "members": len(students)},
        ip=client_ip(request),
    )
    await session.commit()
    return (await _group_out(session, [group]))[0]


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: uuid.UUID, body: GroupPatch, user: Teacher, session: DbSession, request: Request
) -> GroupOut:
    group = await lessons.own_group(session, group_id, user)
    if body.title is not None:
        group.title = body.title.strip()
    if body.student_ids is not None:
        students = await lessons.check_students(session, body.student_ids)
        await lessons.set_members(session, group, students)
    await write_audit(
        session,
        action="group.update",
        actor_id=user.id,
        actor_role=user.role,
        entity="group",
        entity_id=str(group.id),
        details=body.model_dump(exclude_none=True, mode="json"),
        ip=client_ip(request),
    )
    await session.commit()
    return (await _group_out(session, [group]))[0]


# ---------------------------------------------------------------- sessions


async def _list_items(session: DbSession, rows: list[TrainingSession]) -> list[SessionListItem]:
    groups = {
        g.id: g
        for g in await session.scalars(
            select(Group).where(Group.id.in_({r.group_id for r in rows if r.group_id}))
        )
    }
    counts = await lessons.attempt_counts(session, [r.id for r in rows])
    members = await lessons.group_members(session, list(groups))
    items = []
    for ts in rows:
        stats = counts.get(ts.id, {})
        items.append(
            SessionListItem(
                id=ts.id,
                title=ts.title,
                mode=ts.mode,
                status=ts.status,
                group_id=ts.group_id,
                group_title=groups[ts.group_id].title if ts.group_id in groups else "",
                difficulty=ts.difficulty,
                created_at=ts.created_at,
                started_at=ts.started_at,
                finished_at=ts.finished_at,
                students=len(members.get(ts.group_id, [])),
                evaluated=int(stats.get("evaluated", 0)),
                average=None if stats.get("average") is None else round(stats["average"], 1),
            )
        )
    return items


async def _session_out(session: DbSession, ts: TrainingSession) -> SessionOut:
    item = (await _list_items(session, [ts]))[0]
    members = (await lessons.group_members(session, [ts.group_id] if ts.group_id else [])).get(
        ts.group_id, []
    )
    queue = await lessons.pick_scenarios(session, ts)
    return SessionOut(
        **item.model_dump(),
        card_source=ts.card_source,
        scenario_ids=list(ts.scenario_ids),
        incident_groups=list(ts.incident_groups),
        service_profile=list(ts.service_profile),
        norm_seconds=ts.norm_seconds,
        pass_threshold=ts.pass_threshold,
        hints_enabled=ts.hints_enabled,
        cards_per_student=ts.cards_per_student,
        unfinished_seconds=ts.unfinished_seconds,
        weights=dict(ts.weights or {}),
        members=[_student_out(u) for u in members],
        queue=[_queue_out(s) for s in queue],
        last_seq=await present.last_seq(session, ts.id),
    )


def _queue_out(s: Scenario) -> QueueScenarioOut:
    return QueueScenarioOut(
        id=s.id,
        title=s.title,
        difficulty=s.difficulty,
        service_code=s.service_code,
        incident_type_code=s.incident_type_code,
    )


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions(
    user: Teacher, session: DbSession, status: str | None = None
) -> list[SessionListItem]:
    """The teacher's own sessions, newest first."""
    query = (
        select(TrainingSession)
        .where(TrainingSession.teacher_id == user.id)
        .order_by(TrainingSession.created_at.desc())
    )
    if status:
        query = query.where(TrainingSession.status == status)
    return await _list_items(session, list(await session.scalars(query)))


@router.post("/sessions", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionIn, user: Teacher, session: DbSession, request: Request
) -> SessionOut:
    spec = lessons.SessionSettings(**body.model_dump())
    await lessons.validate_settings(session, spec, user)
    ts = TrainingSession(teacher_id=user.id, mode=spec.mode, title=spec.title)
    lessons.apply_settings(ts, spec)
    session.add(ts)
    await session.flush()
    await write_audit(
        session,
        action="session.create",
        actor_id=user.id,
        actor_role=user.role,
        entity="training_session",
        entity_id=str(ts.id),
        details={"title": ts.title, "group_id": str(ts.group_id)},
        ip=client_ip(request),
    )
    await session.commit()
    return await _session_out(session, ts)


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(session_id: uuid.UUID, user: Teacher, session: DbSession) -> SessionOut:
    ts = await lessons.own_session(session, session_id, user)
    return await _session_out(session, ts)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def update_session(
    session_id: uuid.UUID,
    body: SessionPatch,
    user: Teacher,
    session: DbSession,
    request: Request,
) -> SessionOut:
    """Settings change only while the session is a draft; the title and hints may change
    at any time."""
    ts = await lessons.own_session(session, session_id, user)
    changes = body.model_dump(exclude_none=True)
    if ts.status != SESSION_DRAFT and set(changes) - {"title", "hints_enabled"}:
        raise ApiError(
            409, "session_started", "Занятие уже начато: менять можно только название и подсказки."
        )
    current = {name: getattr(ts, name) for name in lessons.SessionSettings.__dataclass_fields__}
    spec = lessons.SessionSettings(**{**current, **changes})
    await lessons.validate_settings(session, spec, user)
    lessons.apply_settings(ts, spec)
    await write_audit(
        session,
        action="session.update",
        actor_id=user.id,
        actor_role=user.role,
        entity="training_session",
        entity_id=str(ts.id),
        details=body.model_dump(exclude_none=True, mode="json"),
        ip=client_ip(request),
    )
    await session.commit()
    return await _session_out(session, ts)


@router.post("/sessions/{session_id}/start", response_model=SessionOut)
async def start_session(
    session_id: uuid.UUID, user: Teacher, session: DbSession, request: Request
) -> SessionOut:
    """Starts the lesson: the group sees it as active and gets cards on opening the journal."""
    ts = await lessons.own_session(session, session_id, user)
    events = await lessons.start_session(session, ts, user)
    if events:
        await write_audit(
            session,
            action="session.start",
            actor_id=user.id,
            actor_role=user.role,
            entity="training_session",
            entity_id=str(ts.id),
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(events)
    return await _session_out(session, ts)


@router.post("/sessions/{session_id}/finish", response_model=SessionOut)
async def finish_session(
    session_id: uuid.UUID, user: Teacher, session: DbSession, request: Request
) -> SessionOut:
    """Finishes the lesson: open cards are closed and scored, untouched ones withdrawn."""
    ts = await lessons.own_session(session, session_id, user)
    events = await lessons.finish_session(session, ts, user)
    if events:
        await write_audit(
            session,
            action="session.finish",
            actor_id=user.id,
            actor_role=user.role,
            entity="training_session",
            entity_id=str(ts.id),
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(events)
    return await _session_out(session, ts)


@router.get("/sessions/{session_id}/monitor", response_model=MonitorOut)
async def monitor_session(session_id: uuid.UUID, user: Teacher, session: DbSession) -> MonitorOut:
    """Snapshot for the live monitoring; the WebSocket keeps it current."""
    ts = await lessons.own_session(session, session_id, user)
    return await reporting.build_monitor(session, ts)


@router.get("/sessions/{session_id}/report", response_model=ReportOut)
async def session_report(session_id: uuid.UUID, user: Teacher, session: DbSession) -> ReportOut:
    ts = await lessons.own_session(session, session_id, user)
    return await reporting.build_report(session, ts)
