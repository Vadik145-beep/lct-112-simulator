"""«Вал карточек» (issue #264): in a card response lesson at difficulty 3
(``service.is_stream``) cards reach the trainee on a timer instead of when the journal asks.

Each trainee has his own schedule, fixed by the lesson and the trainee: an opening burst of two
or three cards, then one card every ``60 / STREAM_PER_MINUTE`` seconds give or take
``JITTER``, until ``STREAM_MINUTES`` pass. The sweeper (app.training.sweeper) issues the cards
that are due; fewer when the queue runs out.

A repeated call (a scenario with ``duplicate_of``, the number of the earlier card) comes as in
life: ``REPEAT_DELAY`` seconds after its original reached the trainee, outside the schedule.
"""

from __future__ import annotations

import bisect
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.insights import plural
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    MODE_CARD_RESPONSE,
    SESSION_RUNNING,
    Attempt,
    Scenario,
    ScenarioVersion,
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
# Seconds between a card and the repeated call about the same incident.
REPEAT_DELAY = (60.0, 120.0)


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


def repeat_delay(ts: TrainingSession, student_id: uuid.UUID, scenario_id: uuid.UUID) -> float:
    """Seconds after the original at which this trainee gets the repeated call."""
    rng = random.Random(f"{ts.id}:{student_id}:{scenario_id}")  # noqa: S311 - not security
    return rng.uniform(*REPEAT_DELAY)


async def repeats_of(session: AsyncSession, queue: list[Scenario]) -> dict[uuid.UUID, str]:
    """Repeated calls of the queue: scenario → number of its original card, only when the
    original is in the queue too (otherwise the repeat is an ordinary card)."""
    if not queue:
        return {}
    versions = {s.id: s.current_version for s in queue}
    rows = await session.scalars(
        select(ScenarioVersion).where(ScenarioVersion.scenario_id.in_(list(versions)))
    )
    bodies = {v.scenario_id: v.body for v in rows if versions.get(v.scenario_id) == v.version}
    numbers = {str((b.get("card") or {}).get("number") or "") for b in bodies.values()}
    return {
        sid: str(b["duplicate_of"])
        for sid, b in bodies.items()
        if b.get("duplicate_of") and str(b["duplicate_of"]) in numbers
    }


@dataclass
class IssuedCard:
    scenario_id: uuid.UUID
    card_number: str
    issued_at: datetime


def ready_repeats(
    ts: TrainingSession,
    student_id: uuid.UUID,
    repeats: dict[uuid.UUID, str],
    issued: list[IssuedCard],
    now: datetime,
) -> set[uuid.UUID]:
    """Repeated calls whose original reached the trainee long enough ago and that he has not
    got yet."""
    got = {i.scenario_id for i in issued}
    first_at: dict[str, datetime] = {}
    for i in issued:
        first_at.setdefault(i.card_number, i.issued_at)
    ready: set[uuid.UUID] = set()
    for sid, original in repeats.items():
        at = first_at.get(original)
        if sid in got or at is None:
            continue
        if now >= at + timedelta(seconds=repeat_delay(ts, student_id, sid)):
            ready.add(sid)
    return ready


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
        if ts.group_id is None or ts.started_at is None or not training.is_stream(ts):
            continue
        elapsed = (now - ts.started_at).total_seconds()
        issued: dict[uuid.UUID, list[IssuedCard]] = {}
        for student_id, sid, number, at in await session.execute(
            select(Attempt.student_id, Attempt.scenario_id, Attempt.card_number, Attempt.issued_at)
            .where(Attempt.session_id == ts.id)
            .order_by(Attempt.issued_at)
        ):
            issued.setdefault(student_id, []).append(IssuedCard(sid, number, at))
        repeats = await repeats_of(session, await training.scenario_queue(session, ts))
        members = (await group_members(session, [ts.group_id])).get(ts.group_id, [])
        for student in members:
            mine = issued.get(student.id, [])
            # A repeated call comes on its own time and takes no place in the schedule.
            ready = ready_repeats(ts, student.id, repeats, mine, now)
            if ready:
                _, issued_events = await training.issue_next(
                    session, ts, student, len(ready), hold=lambda s, ok=ready: s.id not in ok
                )
                events.extend(issued_events)
            in_schedule = sum(1 for i in mine if i.scenario_id not in repeats)
            want = due_count(trainee_schedule(ts, student.id), elapsed) - in_schedule
            if want > 0:
                _, issued_events = await training.issue_next(
                    session, ts, student, want, hold=lambda s, held=repeats: s.id in held
                )
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
