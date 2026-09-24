"""«Мой прогресс» of the trainee (PRD 13.7): the score per lesson, time against the norm,
frequent errors and what to read in the memo, plus the skill ratings by incident group and
the weekly dynamics from the analytics (``app.analytics.service.progress_extras``)."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import service as analytics
from app.analytics.schemas import RatingOut, WeekPointOut
from app.domain.analytics.records import MAX_TIME_RATIO
from app.models import (
    MODE_CALL_INTAKE,
    SESSION_DRAFT,
    Attempt,
    TrainingSession,
    TypicalError,
    User,
)
from app.training import review
from app.training import service as training

MAX_FREQUENT_ERRORS = 5
MAX_RECOMMENDATIONS = 4
# Share of timed attempts over the norm that turns the pace into a recommendation.
LATE_SHARE = 0.3


class ProgressSession(BaseModel):
    session_id: uuid.UUID
    title: str
    mode: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    norm_seconds: int
    pass_threshold: int
    attempts: int
    evaluated: int
    passed: int
    average: float | None
    average_seconds: float | None
    average_deviation: float | None
    comments: int
    overridden: int


class FrequentError(BaseModel):
    code: str
    title: str
    count: int
    memo_ref: str | None


class ProgressOut(BaseModel):
    sessions: list[ProgressSession]
    attempts: int
    evaluated: int
    passed: int
    average: float | None
    average_seconds: float | None
    frequent_errors: list[FrequentError]
    recommendations: list[str]
    # Analytics (PRD 9.7): ratings from the weakest, mean score and time by week, and whether
    # the history is the demonstration seed.
    ratings: list[RatingOut]
    dynamics: list[WeekPointOut]
    demo_data: bool


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


async def build_progress(session: AsyncSession, student: User) -> ProgressOut:
    attempts = list(
        await session.scalars(
            select(Attempt).where(Attempt.student_id == student.id).order_by(Attempt.issued_at)
        )
    )
    session_ids = {a.session_id for a in attempts}
    sessions = {
        ts.id: ts
        for ts in await session.scalars(
            select(TrainingSession).where(TrainingSession.id.in_(session_ids))
        )
    }
    attempt_ids = [a.id for a in attempts]
    overrides = await review.latest_overrides(session, attempt_ids)
    comments = await review.comments_for(session, attempt_ids)

    by_session: dict[uuid.UUID, list[Attempt]] = {}
    for a in attempts:
        by_session.setdefault(a.session_id, []).append(a)

    rows: list[ProgressSession] = []
    all_totals: list[float] = []
    all_seconds: list[float] = []
    all_passed = 0
    errors: Counter = Counter()
    error_titles: dict[str, str] = {}
    error_refs: dict[str, str | None] = {}
    # Overdue and timed attempts per mode: the pace advice differs for a card and a call.
    late: Counter = Counter()
    timed: Counter = Counter()
    for sid, rows_of in by_session.items():
        ts = sessions.get(sid)
        if ts is None or ts.status == SESSION_DRAFT:
            continue
        totals = [float(a.result["total"]) for a in rows_of if a.result]
        seconds = [s for s in (training.attempt_seconds(a) for a in rows_of) if s is not None]
        # An attempt left open for hours must not drag the average: above MAX_TIME_RATIO norms
        # the time is equally bad anyway, so it counts as that much (analytics.records).
        limit = MAX_TIME_RATIO * ts.norm_seconds if ts.norm_seconds > 0 else None
        counted = seconds if limit is None else [min(s, limit) for s in seconds]
        passed = sum(1 for a in rows_of if a.result and a.result.get("passed"))
        mean_seconds = _mean(counted)
        # «Late» is counted on the real time: the cap changes how much, not whether.
        late[ts.mode] += sum(1 for s in seconds if s > ts.norm_seconds)
        timed[ts.mode] += len(seconds)
        for a in rows_of:
            for e in (a.result or {}).get("errors") or []:
                code = str(e.get("code") or e.get("title"))
                errors[code] += 1
                error_titles[code] = str(e.get("title") or code)
                error_refs[code] = e.get("memo_ref")
        rows.append(
            ProgressSession(
                session_id=ts.id,
                title=ts.title,
                mode=ts.mode,
                status=ts.status,
                started_at=ts.started_at,
                finished_at=ts.finished_at,
                norm_seconds=ts.norm_seconds,
                pass_threshold=ts.pass_threshold,
                attempts=len(rows_of),
                evaluated=len(totals),
                passed=passed,
                average=_mean(totals),
                average_seconds=mean_seconds,
                average_deviation=None
                if mean_seconds is None
                else round(mean_seconds - ts.norm_seconds, 1),
                comments=sum(len(comments.get(a.id, [])) for a in rows_of),
                overridden=sum(1 for a in rows_of if a.id in overrides),
            )
        )
        all_totals += totals
        all_seconds += counted
        all_passed += passed
    rows.sort(key=lambda r: r.started_at.timestamp() if r.started_at else 0, reverse=True)

    frequent = [
        FrequentError(code=c, title=error_titles[c], count=n, memo_ref=error_refs.get(c))
        for c, n in errors.most_common(MAX_FREQUENT_ERRORS)
    ]
    # Memo references of the typical errors table are fuller than what the result carries.
    codes = [f.code for f in frequent if not f.memo_ref]
    if codes:
        for te in await session.scalars(select(TypicalError).where(TypicalError.code.in_(codes))):
            for f in frequent:
                if f.code == te.code:
                    f.memo_ref = te.memo_ref
    extras = await analytics.progress_extras(session, student)
    recommendations = _recommendations(frequent, late, timed, all_totals)
    if extras.weakest_tip:
        recommendations = [extras.weakest_tip, *recommendations][:MAX_RECOMMENDATIONS]
    return ProgressOut(
        sessions=rows,
        attempts=len(attempts),
        evaluated=len(all_totals),
        passed=all_passed,
        average=_mean(all_totals),
        average_seconds=_mean(all_seconds),
        frequent_errors=frequent,
        recommendations=recommendations,
        ratings=extras.ratings,
        dynamics=extras.dynamics,
        demo_data=extras.demo_data,
    )


def _overdue_mode(late: Counter, timed: Counter) -> str | None:
    """The mode the trainee is slowest in, or ``None`` while the pace is within the norm."""
    over = [mode for mode, total in timed.items() if total and late[mode] / total > LATE_SHARE]
    return max(over, key=lambda mode: late[mode]) if over else None


def _recommendations(
    frequent: list[FrequentError], late: Counter, timed: Counter, totals: list[float]
) -> list[str]:
    tips: list[str] = []
    for f in frequent[:2]:
        if f.memo_ref:
            tips.append(f"«{f.title}» повторяется ({f.count}): перечитайте памятку, {f.memo_ref}.")
        else:
            tips.append(f"«{f.title}» повторяется ({f.count}): разберите примеры в справочнике.")
    overdue = _overdue_mode(late, timed)
    if overdue == MODE_CALL_INTAKE:
        tips.append(
            "Карточка часто сохраняется позже норматива: спрашивайте по опросной карте "
            "и заполняйте поля во время разговора, а не после него."
        )
    elif overdue is not None:
        tips.append(
            "Первичный статус часто ставится позже норматива: открывайте карточку сразу "
            "после «Добавлена», сначала ставьте «Принята» или «Не принята», потом остальное."
        )
    if totals and sum(totals) / len(totals) < 70:
        tips.append(
            "Средний балл ниже порога зачёта: пройдите занятие ещё раз с подсказками и "
            "сверяйте свои действия с эталоном в разборе."
        )
    if not tips and totals:
        tips.append("Ошибок мало: продолжайте в том же темпе, попробуйте занятия сложнее.")
    return tips[:MAX_RECOMMENDATIONS]
