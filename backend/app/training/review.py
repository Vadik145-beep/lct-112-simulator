"""Teacher's part of the review (PRD 11, 13.6): changing the total with a reason and
commenting an attempt. Both are visible to the trainee and written to the audit log; the
override also keeps the old value so the review shows it struck through."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.auth.deps import DbSession, client_ip, require_role
from app.errors import ApiError
from app.events import append_event, publish_events
from app.models import (
    Attempt,
    Comment,
    Evaluation,
    EvaluationOverride,
    Role,
    TrainingSession,
    User,
)
from app.training import service as training
from app.training.schemas import CommentOut, OverrideOut

router = APIRouter(tags=["review"])

Teacher = Annotated[User, Depends(require_role(Role.teacher))]


class OverrideIn(BaseModel):
    new_total: float = Field(ge=0, le=100)
    reason: str = Field(min_length=3, max_length=2000)


class CommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


# ---------------------------------------------------------------- reading


async def latest_overrides(
    session: AsyncSession, attempt_ids: list[uuid.UUID]
) -> dict[uuid.UUID, EvaluationOverride]:
    """The newest override per attempt (what the review and the report show)."""
    if not attempt_ids:
        return {}
    rows = await session.scalars(
        select(EvaluationOverride)
        .where(EvaluationOverride.attempt_id.in_(attempt_ids))
        .order_by(EvaluationOverride.at)
    )
    latest: dict[uuid.UUID, EvaluationOverride] = {}
    for row in rows:
        latest[row.attempt_id] = row
    return latest


async def comments_for(
    session: AsyncSession, attempt_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Comment]]:
    if not attempt_ids:
        return {}
    rows = await session.scalars(
        select(Comment).where(Comment.attempt_id.in_(attempt_ids)).order_by(Comment.at)
    )
    by_attempt: dict[uuid.UUID, list[Comment]] = {}
    for row in rows:
        by_attempt.setdefault(row.attempt_id, []).append(row)  # type: ignore[arg-type]
    return by_attempt


async def _names(session: AsyncSession, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return {u.id: u.full_name for u in await session.scalars(select(User).where(User.id.in_(ids)))}


async def review_extras(
    session: AsyncSession, attempt: Attempt
) -> tuple[OverrideOut | None, list[CommentOut]]:
    """What the attempt page adds to the evaluation: the override and the comments."""
    overrides = await latest_overrides(session, [attempt.id])
    comments = (await comments_for(session, [attempt.id])).get(attempt.id, [])
    override = overrides.get(attempt.id)
    ids = {c.author_id for c in comments if c.author_id}
    if override and override.teacher_id:
        ids.add(override.teacher_id)
    names = await _names(session, ids)
    override_out = None
    if override is not None:
        override_out = OverrideOut(
            old_total=override.old_total,
            old_passed=override.old_passed,
            new_total=override.new_total,
            new_passed=override.new_passed,
            reason=override.reason,
            teacher_name=names.get(override.teacher_id, "") if override.teacher_id else "",
            at=override.at,
        )
    return override_out, [
        CommentOut(
            id=c.id,
            text=c.text,
            author_name=names.get(c.author_id, "") if c.author_id else "",
            at=c.at,
        )
        for c in comments
    ]


# ---------------------------------------------------------------- writing


async def _teachers_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, user: User
) -> tuple[Attempt, TrainingSession]:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    ts = await session.get(TrainingSession, attempt.session_id)
    assert ts is not None
    return attempt, ts


@router.patch("/attempts/{attempt_id}/evaluation", response_model=OverrideOut)
async def override_evaluation(
    attempt_id: uuid.UUID, body: OverrideIn, user: Teacher, session: DbSession, request: Request
) -> OverrideOut:
    """Replaces the total of a closed attempt. The reason is mandatory and goes to the audit
    log; the trainee sees the old total struck through and the reason."""
    attempt, ts = await _teachers_attempt(session, attempt_id, user)
    evaluation = await session.get(Evaluation, attempt.id)
    if evaluation is None or attempt.state not in training.CLOSED_STATES:
        raise ApiError(409, "not_evaluated", "Оценки ещё нет: карточка не закрыта.")
    reason = body.reason.strip()
    if len(reason) < 3:
        raise ApiError(422, "reason_required", "Укажите причину изменения оценки.")
    new_total = round(body.new_total, 1)
    new_passed = new_total >= ts.pass_threshold
    override = EvaluationOverride(
        attempt_id=attempt.id,
        old_total=evaluation.total,
        new_total=new_total,
        old_passed=evaluation.passed,
        new_passed=new_passed,
        reason=reason,
        teacher_id=user.id,
    )
    session.add(override)
    evaluation.total = new_total
    evaluation.passed = new_passed
    result = dict(attempt.result or {})
    result["total"] = new_total
    result["passed"] = new_passed
    attempt.result = result
    await session.flush()
    event = await append_event(
        session,
        session_id=ts.id,
        type_="evaluation.overridden",
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "total": new_total,
            "passed": new_passed,
            "old_total": override.old_total,
        },
    )
    await write_audit(
        session,
        action="evaluation.override",
        actor_id=user.id,
        actor_role=user.role,
        entity="attempt",
        entity_id=str(attempt.id),
        details={"old_total": override.old_total, "new_total": new_total, "reason": reason},
        ip=client_ip(request),
    )
    await session.commit()
    await publish_events([event])
    return OverrideOut(
        old_total=override.old_total,
        old_passed=override.old_passed,
        new_total=new_total,
        new_passed=new_passed,
        reason=reason,
        teacher_name=user.full_name,
        at=override.at,
    )


@router.post("/attempts/{attempt_id}/comments", response_model=CommentOut, status_code=201)
async def add_comment(
    attempt_id: uuid.UUID, body: CommentIn, user: Teacher, session: DbSession, request: Request
) -> CommentOut:
    attempt, ts = await _teachers_attempt(session, attempt_id, user)
    text = body.text.strip()
    if not text:
        raise ApiError(422, "text_required", "Комментарий пустой.")
    comment = Comment(attempt_id=attempt.id, author_id=user.id, text=text)
    session.add(comment)
    await session.flush()
    event = await append_event(
        session,
        session_id=ts.id,
        type_="attempt.commented",
        student_id=attempt.student_id,
        payload={"attempt_id": attempt.id, "comment_id": comment.id},
    )
    await write_audit(
        session,
        action="attempt.comment",
        actor_id=user.id,
        actor_role=user.role,
        entity="attempt",
        entity_id=str(attempt.id),
        details={"comment_id": str(comment.id)},
        ip=client_ip(request),
    )
    await session.commit()
    await publish_events([event])
    return CommentOut(id=comment.id, text=comment.text, author_name=user.full_name, at=comment.at)
