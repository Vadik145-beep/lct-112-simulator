"""State of the call of a call-intake attempt (PRD 9.5): ringing → answered → ended, plus the
operator's marks «нет контакта» and «срыв звонка». The same transitions serve the SIP call
(``app.telephony``) and the browser fallback without telephony; each one appends a
``call.*`` event inside the caller's transaction.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.domain.evaluation.schemas import CallIntakeScenario
from app.errors import ApiError
from app.events import append_event
from app.models import (
    CALL_ANSWERED,
    CALL_END_CALLER_HANGUP,
    CALL_ENDED,
    CALL_IDLE,
    CALL_RINGING,
    Attempt,
    ScenarioVersion,
    SessionEvent,
    TrainingSession,
)
from app.training import service as training

EVENT_CALL_RINGING = "call.ringing"
EVENT_CALL_ANSWERED = "call.answered"
EVENT_CALL_ENDED = "call.ended"

# A caller who «бросает трубку» does it after this many operator questions (seed scenario
# 2-2: «после второго вопроса бросает трубку»).
DROP_AFTER_OPERATOR_TURNS = 2


def _payload(attempt: Attempt, **extra: object) -> dict:
    return {
        "attempt_id": attempt.id,
        "call_state": attempt.call_state,
        "call_end_reason": attempt.call_end_reason,
        "call_ended_at": attempt.call_ended_at,
        "answered_at": attempt.answered_at,
        "call_dropped_marked": attempt.call_dropped_marked,
        "no_contact_marked": attempt.no_contact_marked,
        **extra,
    }


async def _event(
    session: AsyncSession, attempt: Attempt, type_: str, **extra: object
) -> SessionEvent:
    return await append_event(
        session,
        session_id=attempt.session_id,
        type_=type_,
        student_id=attempt.student_id,
        payload=_payload(attempt, **extra),
    )


async def mark_ringing(session: AsyncSession, attempt: Attempt) -> list[SessionEvent]:
    if attempt.call_state != CALL_IDLE:
        return []
    attempt.call_state = CALL_RINGING
    return [await _event(session, attempt, EVENT_CALL_RINGING)]


async def answer(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CallIntakeScenario,
    *,
    telephony: bool,
) -> tuple[dict, list[SessionEvent]]:
    """The operator picked up: the caller's opening becomes turn 0 (voiced when a voice is
    available) and the attempt is «в разговоре». Idempotent."""
    from app.dialog import service as dialog

    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "attempt_closed", "Вызов уже завершён.")
    if attempt.call_state == CALL_ENDED:
        raise ApiError(409, "call_ended", "Звонок уже завершён, ответить нельзя.")
    now = training.utcnow()
    was_answered = attempt.call_state == CALL_ANSWERED
    opening = await dialog.ensure_opening(attempt, version, scenario, now)
    events: list[SessionEvent] = []
    if not was_answered:
        attempt.call_state = CALL_ANSWERED
        events.append(await _event(session, attempt, EVENT_CALL_ANSWERED, telephony=telephony))
    await session.flush()
    return opening, events


async def end(
    session: AsyncSession, attempt: Attempt, reason: str, at: datetime | None = None
) -> list[SessionEvent]:
    """Ends the call once; later calls with another reason are ignored."""
    if attempt.call_state == CALL_ENDED:
        return []
    attempt.call_state = CALL_ENDED
    attempt.call_end_reason = reason
    attempt.call_ended_at = at or training.utcnow()
    return [await _event(session, attempt, EVENT_CALL_ENDED, reason=reason)]


async def mark_no_contact(session: AsyncSession, attempt: Attempt) -> list[SessionEvent]:
    attempt.no_contact_marked = True
    return await end(session, attempt, "no_contact")


async def mark_call_dropped(session: AsyncSession, attempt: Attempt) -> list[SessionEvent]:
    attempt.call_dropped_marked = True
    return await end(session, attempt, "call_dropped")


def operator_turns(attempt: Attempt) -> int:
    return sum(1 for turn in attempt.dialog if turn.get("role") == "operator")


async def caller_drops_after_turn(
    session: AsyncSession, attempt: Attempt, scenario: CallIntakeScenario
) -> list[SessionEvent]:
    """Scenario «бросил трубку»: the caller hangs up after the reply to the N-th question."""
    if not scenario.caller.drops_call or attempt.call_state == CALL_ENDED:
        return []
    if operator_turns(attempt) < DROP_AFTER_OPERATOR_TURNS:
        return []
    flag_modified(attempt, "dialog")
    return await end(session, attempt, CALL_END_CALLER_HANGUP)
