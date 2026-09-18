"""Trainee API of the card-response mode: assignments, journal, opening a card, setting
statuses, closing a card (PRD section 11)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import delete, func, select

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip
from app.config import get_settings
from app.domain.evaluation.call_intake import description_text
from app.domain.evaluation.card_response import comment_texts
from app.domain.evaluation.schemas import CallIntakeAttempt, CardResponseAttempt
from app.errors import ApiError
from app.events import append_event, publish_events
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    MODE_CALL_INTAKE,
    Attempt,
    Evaluation,
    Role,
    SessionEvent,
    TrainingSession,
    User,
)
from app.training import present, review
from app.training import service as training
from app.training.schemas import (
    AssignmentOut,
    AttemptOut,
    JournalOut,
    StatusRequest,
    StatusResponse,
)
from app.training.teacher_schemas import ProgressRequest

router = APIRouter(tags=["training"])

JOURNAL_PAGE_SIZES = (10, 20, 50)


@router.get("/me/assignments", response_model=list[AssignmentOut])
async def my_assignments(user: ActiveUser, session: DbSession) -> list[AssignmentOut]:
    """Sessions of the trainee's groups, running ones first."""
    if user.role != Role.student:
        raise ApiError(403, "forbidden", "Задания есть только у обучающихся.")
    sessions = await training.sessions_for_student(session, user)
    if not sessions:
        return []
    lookups = await present.load_lookups(session, set())
    counts = await session.execute(
        select(Attempt.session_id, Attempt.state, func.count())
        .where(
            Attempt.student_id == user.id,
            Attempt.session_id.in_([s.id for s in sessions]),
        )
        .group_by(Attempt.session_id, Attempt.state)
    )
    active: dict[uuid.UUID, int] = {}
    finished: dict[uuid.UUID, int] = {}
    for session_id, state, count in counts:
        if state in ACTIVE_ATTEMPT_STATES:
            active[session_id] = active.get(session_id, 0) + count
        else:
            finished[session_id] = finished.get(session_id, 0) + count
    # Running first, then upcoming, then finished; the newest lesson first inside a group.
    order = {"running": 0, "draft": 1, "finished": 2}
    sessions.sort(key=lambda s: (order.get(s.status, 3), -s.created_at.timestamp()))
    return [
        AssignmentOut(
            **present.session_info(ts, lookups, _own_service(ts, user)).model_dump(),
            active_cards=active.get(ts.id, 0),
            finished_cards=finished.get(ts.id, 0),
        )
        for ts in sessions
    ]


def _own_service(ts: TrainingSession, user: User) -> str | None:
    if user.service_code and (not ts.service_profile or user.service_code in ts.service_profile):
        return user.service_code
    return ts.service_profile[0] if ts.service_profile else user.service_code


async def _journal_student(session: DbSession, ts: TrainingSession, user: User, student_id):
    if user.role == Role.student:
        return user
    if student_id is None:
        raise ApiError(422, "student_required", "Укажите обучающегося (student_id).")
    student = await session.get(User, student_id)
    if student is None:
        raise ApiError(404, "student_not_found", "Обучающийся не найден.")
    return student


@router.get("/sessions/{session_id}/journal", response_model=JournalOut)
async def journal(
    session_id: uuid.UUID,
    user: ActiveUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query()] = 10,
    student_id: uuid.UUID | None = None,
) -> JournalOut:
    """Journal of the trainee's service in a session. Opening it issues the next cards of
    the queue when the trainee has none active (the 30-second norm starts at issue)."""
    if per_page not in JOURNAL_PAGE_SIZES:
        raise ApiError(422, "bad_page_size", f"Записей на странице: {JOURNAL_PAGE_SIZES}.")
    ts = await training.get_session_for(session, session_id, user)
    student = await _journal_student(session, ts, user, student_id)
    events: list = []
    if user.role == Role.student:
        _, events = await training.issue_cards(session, ts, student)
        if events:
            await session.commit()
            await publish_events(events)
    base = select(Attempt).where(Attempt.session_id == ts.id, Attempt.student_id == student.id)
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    attempts = list(
        await session.scalars(
            base.order_by(Attempt.issued_at.desc()).offset((page - 1) * per_page).limit(per_page)
        )
    )
    versions = await present.load_versions(session, attempts)
    bodies = {a.id: versions[(a.scenario_id, a.scenario_version)].body for a in attempts}
    lookups = await present.load_lookups(
        session, {b.get("card", {}).get("incident_type") for b in bodies.values()}
    )
    return JournalOut(
        session=present.session_info(ts, lookups, _own_service(ts, student)),
        arm=present.arm_info(student),
        items=[present.journal_item(a, bodies[a.id], ts, lookups) for a in attempts],
        page=page,
        per_page=per_page,
        total=total,
        last_seq=await present.last_seq(session, ts.id),
    )


async def _attempt_out(session: DbSession, attempt: Attempt, ts: TrainingSession) -> AttemptOut:
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    student = await session.get(User, attempt.student_id)
    lookups = await present.load_lookups(session, {card.body.get("card", {}).get("incident_type")})
    evaluation = None
    if attempt.state in training.CLOSED_STATES:
        row = await session.get(Evaluation, attempt.id)
        if row is not None:
            evaluation = {
                "total": row.total,
                "passed": row.passed,
                "components": row.components,
                "errors": row.errors,
                "ai_comment": row.ai_comment,
                "methods": row.methods,
                # Text the grammar component was checked on; its items carry offsets into it.
                "checked_text": _checked_text(attempt),
            }
    override, comments = await review.review_extras(session, attempt)
    out = present.attempt_out(
        attempt,
        card.body,
        ts,
        student,
        lookups,
        seq=await present.last_seq(session, ts.id),
        evaluation=evaluation,
    )
    out.override = override
    out.comments = comments
    return out


def _checked_text(attempt: Attempt) -> str:
    data = training.evaluation_input(attempt)
    if attempt.mode == MODE_CALL_INTAKE:
        return description_text(CallIntakeAttempt.model_validate(data))
    return comment_texts(CardResponseAttempt.model_validate(data))


@router.get("/attempts/{attempt_id}", response_model=AttemptOut)
async def get_attempt(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> AttemptOut:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    ts = await session.get(TrainingSession, attempt.session_id)
    return await _attempt_out(session, attempt, ts)


@router.post("/attempts/{attempt_id}/open", response_model=AttemptOut)
async def open_attempt(
    attempt_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> AttemptOut:
    """«Получена службой»: called when the dispatcher opens the card (idempotent)."""
    attempt = await _own_attempt(session, attempt_id, user)
    ts = await session.get(TrainingSession, attempt.session_id)
    events = await training.open_attempt(session, attempt, ts)
    if events:
        await write_audit(
            session,
            action="attempt.open",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(events)
    return await _attempt_out(session, attempt, ts)


@router.post("/attempts/{attempt_id}/status", response_model=StatusResponse)
async def set_status(
    attempt_id: uuid.UUID,
    body: StatusRequest,
    user: ActiveUser,
    session: DbSession,
    request: Request,
) -> StatusResponse:
    """Sets a response status with an optional order number and comment. Only transitions
    allowed by the status machine pass; the error says which statuses are available."""
    attempt = await _own_attempt(session, attempt_id, user)
    ts = await session.get(TrainingSession, attempt.session_id)
    change = await training.set_status(
        session,
        attempt,
        ts,
        user,
        status=body.status,
        order_number=body.order_number,
        comment=body.comment,
        reject_reason=body.reject_reason,
        action_id=body.action_id,
    )
    if change.changed:
        await write_audit(
            session,
            action="attempt.status",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            details={"status": body.status, "order_number": body.order_number},
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(change.events)
    return StatusResponse(
        attempt=await _attempt_out(session, attempt, ts),
        applied=change.changed,
        issued=[a.id for a in change.issued],
    )


@router.post("/attempts/{attempt_id}/finish", response_model=StatusResponse)
async def finish_attempt(
    attempt_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> StatusResponse:
    """«Завершить работу с карточкой» from the training panel."""
    attempt = await _own_attempt(session, attempt_id, user)
    ts = await session.get(TrainingSession, attempt.session_id)
    change = await training.finish_attempt(session, attempt, ts, user)
    if change.changed:
        await write_audit(
            session,
            action="attempt.finish",
            actor_id=user.id,
            actor_role=user.role,
            entity="attempt",
            entity_id=str(attempt.id),
            ip=client_ip(request),
        )
        await session.commit()
        await publish_events(change.events)
    return StatusResponse(
        attempt=await _attempt_out(session, attempt, ts),
        applied=change.changed,
        issued=[a.id for a in change.issued],
    )


@router.post("/attempts/{attempt_id}/progress", status_code=204)
async def report_progress(
    attempt_id: uuid.UUID, body: ProgressRequest, user: ActiveUser, session: DbSession
) -> Response:
    """What the trainee is doing in an open card (PRD 12: ``attempt.progress``, the client
    sends it at most every 2 s). Shown on the teacher's monitoring tile, not stored on the
    attempt."""
    attempt = await _own_attempt(session, attempt_id, user)
    if attempt.state in training.CLOSED_STATES:
        return Response(status_code=204)
    event = await append_event(
        session,
        session_id=attempt.session_id,
        type_="attempt.progress",
        student_id=user.id,
        payload={"attempt_id": attempt.id, "stage": body.stage},
    )
    await session.commit()
    await publish_events([event])
    return Response(status_code=204)


async def _own_attempt(session: DbSession, attempt_id: uuid.UUID, user: User) -> Attempt:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if user.role != Role.student or attempt.student_id != user.id:
        raise ApiError(403, "forbidden", "Статусы ставит только обучающийся в своей карточке.")
    return attempt


@router.post("/sessions/{session_id}/restart", status_code=204)
async def restart_session(
    session_id: uuid.UUID, user: ActiveUser, session: DbSession, request: Request
) -> Response:
    """Demo stand only (DEMO_MODE=true): drops the trainee's own cards of a session so the
    exercise can be run again from the first card. Hidden (404) outside demo mode."""
    if not get_settings().demo_mode or user.role != Role.student:
        raise ApiError(404, "not_found", "Не найдено.")
    ts = await training.get_session_for(session, session_id, user)
    await session.execute(
        delete(Attempt).where(Attempt.session_id == ts.id, Attempt.student_id == user.id)
    )
    await session.execute(
        delete(SessionEvent).where(
            SessionEvent.session_id == ts.id, SessionEvent.student_id == user.id
        )
    )
    await write_audit(
        session,
        action="session.restart",
        actor_id=user.id,
        actor_role=user.role,
        entity="training_session",
        entity_id=str(ts.id),
        ip=client_ip(request),
    )
    await session.commit()
    return Response(status_code=204)
