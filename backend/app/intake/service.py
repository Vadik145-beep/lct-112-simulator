"""Operator-112 card of a call-intake attempt (PRD 9.3, 13.5): the autosaved draft and the
final save that closes the attempt and scores it.

The card lives in ``attempts.draft`` (the interface's JSON, see ``schemas.CardIn``) until
«сохранить»; the same JSON is what the evaluation reads afterwards. Like ``training.service``,
every function works inside the caller's transaction and returns the events to publish.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.dialog import call as call_state
from app.errors import ApiError
from app.events import append_event
from app.intake.schemas import CardIn
from app.models import (
    ATTEMPT_FINISHED,
    ATTEMPT_ISSUED,
    ATTEMPT_RECEIVED,
    CALL_END_CARD_SAVED,
    MODE_CALL_INTAKE,
    Attempt,
    Role,
    SessionEvent,
    TrainingSession,
    User,
)
from app.training import service as training

EVENT_DRAFT = "attempt.progress"
STAGE_FILLING = "filling_card"


def card_json(card: CardIn, updated_at: datetime | None = None) -> dict:
    data = card.model_dump()
    data["updated_at"] = (updated_at or training.utcnow()).astimezone(UTC).isoformat()
    return data


def evaluation_card(draft: dict | None) -> dict:
    """``attempts.draft`` as the engine's ``SubmittedCard``: bookkeeping and interface-only
    fields dropped, «Объект» folded into the descriptive address."""
    data = dict(draft or {})
    data.pop("updated_at", None)
    address = dict(data.get("address") or {})
    place = address.pop("object", "")
    if place and not address.get("descriptive"):
        address["descriptive"] = place
    elif place:
        address["descriptive"] = f"{place}, {address['descriptive']}"
    data["address"] = address
    return data


async def own_call_attempt(
    session: AsyncSession, attempt_id, user: User
) -> tuple[Attempt, TrainingSession]:
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CALL_INTAKE:
        raise ApiError(
            409, "not_call_intake", "Карточка оператора 112 есть только в приёме вызова."
        )
    if user.role != Role.student or attempt.student_id != user.id:
        raise ApiError(403, "forbidden", "Заполнять карточку может только обучающийся.")
    ts = await session.get(TrainingSession, attempt.session_id)
    return attempt, ts


async def save_draft(
    session: AsyncSession, attempt: Attempt, card: CardIn, updated_at: datetime | None
) -> tuple[datetime, list[SessionEvent]]:
    """Keeps the card as it is being filled; the teacher's monitoring learns the trainee is
    in the card. A closed attempt keeps its saved card (409)."""
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Карточка уже сохранена: изменить её нельзя.")
    now = training.utcnow()
    attempt.draft = card_json(card, updated_at)
    flag_modified(attempt, "draft")
    if attempt.state == ATTEMPT_ISSUED:
        # Filling the card before answering still means the call was taken on.
        attempt.state = ATTEMPT_RECEIVED
        attempt.received_at = attempt.received_at or now
    event = await append_event(
        session,
        session_id=attempt.session_id,
        type_=EVENT_DRAFT,
        student_id=attempt.student_id,
        payload={"attempt_id": attempt.id, "stage": STAGE_FILLING},
    )
    return now, [event]


@dataclass
class Submission:
    applied: bool
    events: list[SessionEvent]
    issued: list[Attempt]


async def submit_card(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    student: User,
    card: CardIn,
    client_submission_id: str,
) -> Submission:
    """«Сохранить»: the card is final, the attempt closes and is scored at once; the next
    call of the queue rings on the next visit to the calls page. A retry with the same
    ``client_submission_id`` changes nothing and reports ``applied=False``; another id on a
    closed card is 409."""
    if attempt.client_submission_id == client_submission_id:
        return Submission(applied=False, events=[], issued=[])
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Карточка уже сохранена.")
    now = training.utcnow()
    attempt.draft = card_json(card, now)
    flag_modified(attempt, "draft")
    attempt.client_submission_id = client_submission_id
    attempt.received_at = attempt.received_at or now
    attempt.submitted_at = now
    attempt.state = ATTEMPT_FINISHED
    attempt.card_status = training.CARD_FINISHED
    events: list[SessionEvent] = [await training._submitted_event(session, attempt)]
    # «Сохранить» without «Завершить»: the call ends with the card, otherwise the softphone
    # stays in «разговор» while the next card is already issued (docs/BUGS.md, 1).
    events += await call_state.end(session, attempt, CALL_END_CARD_SAVED)
    scenario = await training.load_scenario_card(
        session, attempt.scenario_id, attempt.scenario_version
    )
    events.append(await training.evaluate_and_store(session, attempt, ts, scenario.body))
    # The next call rings when the trainee returns to the calls page (docs/BUGS.md, 9).
    return Submission(applied=True, events=events, issued=[])
