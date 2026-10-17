"""«Вал карточек» (issue #264): in a card response lesson at difficulty 3
(``service.is_stream``) cards reach the trainee on a timer instead of when the journal asks.

Each trainee has his own schedule, fixed by the lesson and the trainee: an opening burst of two
or three cards, then one card every ``60 / STREAM_PER_MINUTE`` seconds give or take
``JITTER``, until ``STREAM_MINUTES`` pass. The sweeper (app.training.sweeper) issues the cards
that are due; fewer when the queue runs out.
"""

from __future__ import annotations

import bisect
import random
import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.insights import plural
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    MODE_CARD_RESPONSE,
    SESSION_RUNNING,
    Attempt,
    SessionEvent,
    TrainingSession,
)
from app.training import present
from app.training import service as training
from app.training.schemas import StreamSummary
from app.training.sessions import group_members

# Cards that arrive together at the start of the stream: the shift does not start empty.
OPENING_BURST = (2, 3)
OPENING_SECONDS = 5.0
# Spread of the interval between cards: real calls do not come on the beat.
JITTER = 0.3


def schedule(seed: str, per_minute: int, minutes: int) -> list[float]:
    """Seconds from the start of the lesson at which each card of the stream arrives."""
    if per_minute <= 0 or minutes <= 0:
        return []
    rng = random.Random(seed)  # noqa: S311 - a training schedule, not security
    total = per_minute * minutes
    end = minutes * 60.0
    burst = min(total, rng.randint(*OPENING_BURST))
    times = sorted(rng.uniform(0, OPENING_SECONDS) for _ in range(burst))
    step = 60.0 / per_minute
    at = times[-1]
    while len(times) < total:
        at += step * rng.uniform(1 - JITTER, 1 + JITTER)
        if at > end:
            break
        times.append(at)
    return times


def due_count(times: list[float], elapsed_seconds: float) -> int:
    """How many cards of the schedule are due ``elapsed_seconds`` after the start."""
    return bisect.bisect_right(times, elapsed_seconds)


def trainee_schedule(ts: TrainingSession, student_id: uuid.UUID) -> list[float]:
    return schedule(f"{ts.id}:{student_id}", training.STREAM_PER_MINUTE, training.STREAM_MINUTES)


async def issue_stream_cards(session: AsyncSession, now: datetime) -> list[SessionEvent]:
    """Issues the cards that are due in every running stream lesson. The lesson row is locked
    with ``SKIP LOCKED`` so two sweepers never issue the same card twice."""
    lessons = list(
        await session.scalars(
            select(TrainingSession)
            .where(
                TrainingSession.status == SESSION_RUNNING,
                TrainingSession.mode == MODE_CARD_RESPONSE,
                TrainingSession.difficulty == training.STREAM_DIFFICULTY,
                TrainingSession.started_at.is_not(None),
            )
            .with_for_update(skip_locked=True)
        )
    )
    events: list[SessionEvent] = []
    for ts in lessons:
        if ts.group_id is None or ts.started_at is None:
            continue
        elapsed = (now - ts.started_at).total_seconds()
        issued = dict(
            (
                await session.execute(
                    select(Attempt.student_id, func.count())
                    .where(Attempt.session_id == ts.id)
                    .group_by(Attempt.student_id)
                )
            ).all()
        )
        members = (await group_members(session, [ts.group_id])).get(ts.group_id, [])
        for student in members:
            want = due_count(trainee_schedule(ts, student.id), elapsed) - issued.get(student.id, 0)
            if want > 0:
                _, issued_events = await training.issue_next(session, ts, student, want)
                events.extend(issued_events)
    return events


async def emergency_attempts(session: AsyncSession, attempts: list[Attempt]) -> set[uuid.UUID]:
    """The attempts whose card is marked ЧС."""
    versions = await present.load_versions(session, attempts)
    found: set[uuid.UUID] = set()
    for a in attempts:
        version = versions.get((a.scenario_id, a.scenario_version))
        flags = ((version.body if version else {}).get("card") or {}).get("flags") or {}
        if flags.get(present.FLAG_EMERGENCY):
            found.add(a.id)
    return found


def _emergency_first(attempts: list[Attempt], emergency: set[uuid.UUID]) -> bool | None:
    """Whether every ЧС card was taken before any ordinary card that waited with it."""
    urgent = [a for a in attempts if a.id in emergency]
    if not urgent:
        return None
    taken = [a.primary_status_at for a in attempts if a.id not in emergency and a.primary_status_at]
    for card in urgent:
        if card.primary_status_at is None:
            return False
        if any(card.issued_at <= at < card.primary_status_at for at in taken):
            return False
    return True


def stream_summary(
    attempts: list[Attempt], emergency: set[uuid.UUID], norm_seconds: int
) -> StreamSummary:
    """The trainee's stream in figures with template advice; ``attempts`` are his attempts of
    one stream lesson."""
    reacted = [s for s in (training.attempt_seconds(a) for a in attempts) if s is not None]
    late = sum(1 for s in reacted if s > norm_seconds)
    no_reaction = sum(1 for a in attempts if a.primary_status_at is None)
    first = _emergency_first(attempts, emergency)
    advice: list[str] = []
    if no_reaction:
        left = plural(no_reaction, "карточка осталась", "карточки остались", "карточек осталось")
        advice.append(
            f"{no_reaction} {left} без реакции. При вале сначала отмечайте каждую новую"
            " карточку «Принята» или «Не принята», потом ведите работу по ним."
        )
    if first is False:
        advice.append(
            "Карточку с ЧС взяли позже обычных. При вале первой берите в работу карточку"
            " с отметкой ЧС."
        )
    if late:
        taken = plural(late, "карточка принята", "карточки приняты", "карточек принято")
        advice.append(
            f"{late} {taken} позже норматива ({norm_seconds} с). Отмечайте приём сразу,"
            " как карточка появилась в журнале."
        )
    if not advice:
        advice.append(
            "Вал выдержан: все карточки приняты в норматив" + (", ЧС — первой." if first else ".")
        )
    return StreamSummary(
        arrived=len(attempts),
        closed=sum(1 for a in attempts if a.state not in ACTIVE_ATTEMPT_STATES),
        late=late,
        no_reaction=no_reaction,
        emergency_cards=len([a for a in attempts if a.id in emergency]),
        emergency_first=first,
        advice=advice,
    )
