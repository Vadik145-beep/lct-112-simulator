"""Lessons where the cards are written for each trainee: «Задания формирует ИИ» (issue #265,
recommendations 9–10 of the customer: the system forms the cards, gives them out, evaluates
and advises without the teacher).

The teacher only picks the group and the difficulty and starts the lesson. Each trainee then
gets ``CARDS_PER_TRAINEE`` cards one at a time, each written for him when the previous one is
scored (``app.domain.scenarios.ai_plan`` chooses what, the generation provider writes it, the
template when the model is down or slow). Writing takes seconds, so it runs in the background:
writing of the next card starts as soon as the previous one is scored (``prepare_next``, while
the trainee reads the result), the journal asks for it (``service.issue_next``), gets nothing
while it is still being written and is refreshed by the ``ai_card.ready`` event.

The card of a trainee is a row of ``ai_cards`` (unique per lesson, trainee and number, so two
journal requests never write the same card twice) pointing to an approved scenario of the source
``ai``: such scenarios belong to their lesson and stay out of the library and other lessons.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import ratings
from app.db import SessionLocal
from app.domain.scenarios.ai_plan import CARDS_PER_TRAINEE, EarlierCard, PlanInput, plan_next
from app.errors import ApiError
from app.events import append_event, publish_events
from app.logging import get_logger
from app.models import (
    AI_CARD_FAILED,
    AI_CARD_GENERATING,
    AI_CARD_READY,
    ATTEMPT_EVALUATED,
    MODE_CARD_RESPONSE,
    SCENARIO_APPROVED,
    AiCard,
    Attempt,
    IncidentGroup,
    Scenario,
    TrainingSession,
    User,
)
from app.providers.generation import (
    GenerationResult,
    TemplateGeneration,
    get_generation_provider,
)

log = get_logger(__name__)

CARD_SOURCE_AI = "ai"  # training_sessions.card_source of such a lesson
SCENARIO_SOURCE_AI = "ai"  # scenarios.source of a card written for one trainee
EVENT_READY = "ai_card.ready"
# The model gets this long per card; then the template writes it (the trainee must not wait).
MODEL_SECONDS = 30.0
# A card «being written» for longer was lost (the server restarted): the template rewrites it.
STALE_SECONDS = 90.0
# Difficulty 3 cards are emergencies: the card carries the ЧС mark of the АРМ-112 header.
EMERGENCY_DIFFICULTY = 3
FLAG_EMERGENCY = "emergency"

_tasks: set[asyncio.Task] = set()


def is_ai(ts: TrainingSession) -> bool:
    return ts.card_source == CARD_SOURCE_AI


def cards_for(ts: TrainingSession) -> int:
    return ts.cards_per_student or CARDS_PER_TRAINEE


# ---------------------------------------------------------------- issuing


async def _cards(session: AsyncSession, ts: TrainingSession, student_id: uuid.UUID) -> list[AiCard]:
    return list(
        await session.scalars(
            select(AiCard)
            .where(AiCard.session_id == ts.id, AiCard.student_id == student_id)
            .order_by(AiCard.seq)
        )
    )


async def _issued(
    session: AsyncSession, ts: TrainingSession, student_id: uuid.UUID
) -> set[uuid.UUID]:
    return set(
        await session.scalars(
            select(Attempt.scenario_id).where(
                Attempt.session_id == ts.id, Attempt.student_id == student_id
            )
        )
    )


def _next_seq(rows: list[AiCard], ts: TrainingSession, done: set[uuid.UUID]) -> int | None:
    """Number of the card to write now; ``None`` while one waits to be issued or is being
    written, or when the trainee has all his cards."""
    if any(r.status == AI_CARD_READY and r.scenario_id not in done for r in rows):
        return None
    if any(r.status == AI_CARD_GENERATING for r in rows):
        return None
    if len([r for r in rows if r.status != AI_CARD_FAILED]) >= cards_for(ts):
        return None
    return max((r.seq for r in rows), default=0) + 1


async def prepare_next(session: AsyncSession, ts: TrainingSession, student_id: uuid.UUID) -> None:
    """Starts writing the trainee's next card right after a card is scored."""
    if not is_ai(ts):
        return
    seq = _next_seq(
        await _cards(session, ts, student_id), ts, await _issued(session, ts, student_id)
    )
    if seq is not None:
        spawn(ts.id, student_id, seq)


async def ready_for(
    session: AsyncSession, ts: TrainingSession, student: User, done: list[uuid.UUID]
) -> list[Scenario]:
    """The trainee's next written card, if there is one. Otherwise starts writing it (in the
    background) and returns nothing; a card lost in writing is rewritten by the template at
    once. ``done`` are the scenarios the trainee already had in this lesson."""
    rows = await _cards(session, ts, student.id)
    ready = [r for r in rows if r.status == AI_CARD_READY and r.scenario_id not in set(done)]
    if ready:
        scenario = await session.get(Scenario, ready[0].scenario_id)
        return [scenario] if scenario is not None else []
    writing = [r for r in rows if r.status == AI_CARD_GENERATING]
    if writing:
        row = writing[0]
        if _age_seconds(row.created_at) < STALE_SECONDS:
            return []
        try:
            async with session.begin_nested():
                await _write_into(session, row, template_only=True)
        except Exception:  # the journal must open whatever happened to the card
            log.exception("ai card rewrite failed", session_id=str(ts.id), seq=row.seq)
            row.status = AI_CARD_FAILED
            return []
        scenario = await session.get(Scenario, row.scenario_id) if row.scenario_id else None
        return [scenario] if scenario is not None else []
    seq = _next_seq(rows, ts, set(done))
    if seq is not None:
        spawn(ts.id, student.id, seq)
    return []


def _age_seconds(created_at: datetime) -> float:
    return (datetime.now(UTC) - created_at).total_seconds()


# ---------------------------------------------------------------- writing in the background


def spawn(session_id: uuid.UUID, student_id: uuid.UUID, seq: int) -> None:
    """Writes card ``seq`` of the trainee in the background."""
    task = asyncio.create_task(
        write_card(session_id, student_id, seq), name=f"ai-card-{session_id}-{student_id}-{seq}"
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def spawn_first(session_id: uuid.UUID, student_ids: list[uuid.UUID]) -> None:
    """The first card of every trainee, written as the lesson starts."""
    for student_id in student_ids:
        spawn(session_id, student_id, 1)


async def wait_all(max_seconds: float = 60.0) -> None:
    """Tests and shutdown: let the cards being written finish."""
    while _tasks:
        await asyncio.wait(list(_tasks), timeout=max_seconds)


async def write_card(session_id: uuid.UUID, student_id: uuid.UUID, seq: int) -> None:
    """Takes the slot of card ``seq`` (another request may have taken it: then nothing to do),
    writes the card and tells the trainee's journal it is there."""
    async with SessionLocal() as session:
        row = AiCard(session_id=session_id, student_id=student_id, seq=seq)
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            return
        try:
            events = await _write_into(session, row, template_only=False)
            await session.commit()
        except Exception:  # the trainee must not be left waiting whatever happened
            log.exception("ai card failed", session_id=str(session_id), seq=seq)
            await session.rollback()
            failed = await session.get(AiCard, row.id)
            if failed is not None:
                failed.status = AI_CARD_FAILED
                await session.commit()
            return
    await publish_events(events)


async def _write_into(session: AsyncSession, row: AiCard, *, template_only: bool) -> list:
    """Plans and writes the card of ``row`` inside the caller's transaction; marks it ready
    and appends the event for the trainee's journal."""
    from app.scenarios import service as scenarios

    ts = await session.get(TrainingSession, row.session_id)
    if ts is None:
        raise ApiError(404, "session_not_found", "Занятие не найдено.")
    refs = await scenarios.load_refs(session)
    catalogue = await scenarios.load_catalogue(session)
    plan = plan_next(
        PlanInput(
            difficulty=ts.difficulty,
            seq=row.seq,
            trainee_key=f"{ts.id}|{row.student_id}",
            type_rows=list(refs.incident_types.values()),
            catalogue=catalogue,
            group_titles=await _group_titles(session),
            ratings=await _ratings(session, row.student_id),
            earlier=await _earlier_cards(session, ts.id, row.student_id),
            allowed_groups=list(ts.incident_groups or []),
            services=list(ts.service_profile or []),
            taken_types=await _taken_types(session, ts.id),
        )
    )
    request = dataclasses.replace(plan.request, mistakes_first=plan.prefer)
    ctx = await scenarios._generation_context(session, refs, request.phrase or "")
    ctx = dataclasses.replace(ctx, services=list(ts.service_profile or []))
    result = await _generate(request, ctx, template_only=template_only)
    teacher = await session.get(User, ts.teacher_id)
    meta = {**scenarios._generation_meta(result, request.phrase), "reasons": plan.reasons}
    try:
        loaded = await scenarios.create(
            session,
            _with_marks(result.body, ts.difficulty),
            source=SCENARIO_SOURCE_AI,
            status=SCENARIO_APPROVED,
            author=teacher,
            refs=refs,
            generation=meta,
        )
    except ApiError:
        if result.method == TemplateGeneration.method:
            raise
        # The model's card did not pass the checks: the template's always does.
        result = await TemplateGeneration().generate(request, ctx)
        result.note = "Карточка модели не прошла проверку; использован шаблон."
        meta = {**scenarios._generation_meta(result, request.phrase), "reasons": plan.reasons}
        loaded = await scenarios.create(
            session,
            _with_marks(result.body, ts.difficulty),
            source=SCENARIO_SOURCE_AI,
            status=SCENARIO_APPROVED,
            author=teacher,
            refs=refs,
            generation=meta,
        )
    row.status = AI_CARD_READY
    row.scenario_id = loaded.scenario.id
    row.method = result.method
    row.reasons = plan.reasons
    row.ready_at = datetime.now(UTC)
    await session.flush()
    event = await append_event(
        session,
        session_id=ts.id,
        type_=EVENT_READY,
        student_id=row.student_id,
        payload={"seq": row.seq, "method": result.method},
    )
    return [event]


async def _generate(request, ctx, *, template_only: bool) -> GenerationResult:
    template = TemplateGeneration()
    if template_only:
        return await template.generate(request, ctx)
    try:
        return await asyncio.wait_for(
            get_generation_provider().generate(request, ctx), MODEL_SECONDS
        )
    except TimeoutError:
        result = await template.generate(request, ctx)
        result.note = f"Модель не ответила за {round(MODEL_SECONDS)} с; использован шаблон."
        return result


def _with_marks(body: dict, difficulty: int) -> dict:
    """A difficulty 3 card is an emergency: the ЧС mark in its header."""
    if difficulty < EMERGENCY_DIFFICULTY or body.get("kind") != MODE_CARD_RESPONSE:
        return body
    card = dict(body.get("card") or {})
    card["flags"] = {**(card.get("flags") or {}), FLAG_EMERGENCY: True}
    return {**body, "card": card}


# ---------------------------------------------------------------- what the plan needs


async def _group_titles(session: AsyncSession) -> dict[str, str]:
    return {g.code: g.title for g in await session.scalars(select(IncidentGroup))}


async def _ratings(session: AsyncSession, student_id: uuid.UUID) -> dict[str, float]:
    state = await ratings.load_state(session, student_id)
    return {
        group: value for (group, mode), value in state.ratings.items() if mode == MODE_CARD_RESPONSE
    }


async def _earlier_cards(
    session: AsyncSession, session_id: uuid.UUID, student_id: uuid.UUID
) -> list[EarlierCard]:
    rows = (
        await session.execute(
            select(Attempt, Scenario.incident_type_code)
            .join(Scenario, Scenario.id == Attempt.scenario_id)
            .where(
                Attempt.session_id == session_id,
                Attempt.student_id == student_id,
                Attempt.state == ATTEMPT_EVALUATED,
            )
            .order_by(Attempt.issued_at)
        )
    ).all()
    cards = []
    for attempt, type_code in rows:
        result = attempt.result or {}
        data_check = ((result.get("components") or {}).get("data_check") or {}).get("items")
        cards.append(
            EarlierCard(
                incident_type=str(type_code or ""),
                total=float(result.get("total") or 0),
                passed=bool(result.get("passed")),
                data_check=list(data_check or []),
            )
        )
    return cards


async def _taken_types(session: AsyncSession, session_id: uuid.UUID) -> list[str]:
    return [
        str(code)
        for code in await session.scalars(
            select(Scenario.incident_type_code)
            .join(AiCard, AiCard.scenario_id == Scenario.id)
            .where(AiCard.session_id == session_id)
        )
        if code
    ]
