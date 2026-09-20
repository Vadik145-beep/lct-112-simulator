"""Calls of the dispatcher to a service officer (issue #36, «звено Б → В»).

A card-response attempt may hold several such calls (``attempts.service_calls``), one per
«Позвонить» in the services strip. Each call is a small dialog: the officer greets (turn 0), the
dispatcher passes the facts, the officer confirms or asks. The officer is played by the same
``DialogProvider`` as the caller of a 112 call, on a synthetic caller scenario built from the
card (``app.domain.scenarios.officers``), with the officer prompts and topics
(``DialogContext.role``). Facts passed are counted after every turn for the panel; the
evaluation engine recounts them from the stored turns.

Rows of ``attempts.service_calls``::

    {id, service, started_at, answered, answered_at, ended_at, end_reason, telephony,
     recording_path, dialog: [turns as in attempts.dialog], facts_passed}

Every function works inside the caller's transaction and returns the events it appended.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.dialog import service as dialog
from app.domain.evaluation import service_call as engine
from app.domain.evaluation.schemas import (
    CallIntakeScenario,
    CardResponseScenario,
    DialogTurn,
    ServiceCallRef,
)
from app.domain.scenarios import officers
from app.errors import ApiError
from app.events import append_event
from app.models import (
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_ISSUED,
    ATTEMPT_RECEIVED,
    Attempt,
    ScenarioVersion,
    SessionEvent,
    TrainingSession,
)
from app.providers.dialog import ROLE_OFFICER, CallerReply, DialogContext
from app.training import service as training

EVENT_STARTED = "service_call.started"
EVENT_ANSWERED = "service_call.answered"
EVENT_ENDED = "service_call.ended"
EVENT_TURN = "service_call.turn"
END_HANGUP = "hangup"  # the dispatcher ended the call
END_NO_ANSWER = "no_answer"
END_FAILED = "failed"
END_CARD_CLOSED = "card_closed"  # the card was closed with the call still open
_RETRY_WINDOW = 5


@dataclass
class OfficerTurn:
    operator: dict
    officer: dict
    applied: bool
    pending_reply: bool
    latency_ms: int
    facts_passed: list[str]
    events: list[SessionEvent] = field(default_factory=list)


def calls_of(attempt: Attempt) -> list[dict]:
    return list(attempt.service_calls or [])


def find_call(attempt: Attempt, call_id: str) -> dict:
    for call in calls_of(attempt):
        if call.get("id") == call_id:
            return call
    raise ApiError(404, "service_call_not_found", "Такого звонка в службу нет.")


def open_call(attempt: Attempt) -> dict | None:
    """The call in progress, if any (one at a time)."""
    for call in calls_of(attempt):
        if call.get("ended_at") is None:
            return call
    return None


def _store(attempt: Attempt, call: dict) -> None:
    attempt.service_calls = [c if c.get("id") != call["id"] else call for c in calls_of(attempt)]
    flag_modified(attempt, "service_calls")


def _iso(at: datetime) -> str:
    return at.astimezone(UTC).isoformat()


def _turn(role: str, text: str, topics: list[str], at: datetime, **extra: object) -> dict:
    row = {"role": role, "text": text, "topics": topics, "at": _iso(at)}
    row.update({k: v for k, v in extra.items() if v is not None and v is not False})
    return row


def turns_of(call: dict) -> list[DialogTurn]:
    return [
        DialogTurn(
            role=t["role"], text=t["text"], topics=list(t.get("topics") or []), at=t.get("at")
        )
        for t in call.get("dialog") or []
    ]


def reference_for(scenario: CardResponseScenario, service: str) -> ServiceCallRef:
    """The reference call for the service, or a default one when the reference does not list
    the service (the dispatcher may still call any service in the strip)."""
    for ref in scenario.reference.service_calls:
        if ref.service == service:
            return ref
    return ServiceCallRef(service=service)


def officer_scenario(
    scenario: CardResponseScenario, service: str, service_title: str
) -> CallIntakeScenario:
    return officers.officer_scenario(scenario, reference_for(scenario, service), service_title)


def _context(attempt: Attempt, call: dict, officer: CallIntakeScenario) -> DialogContext:
    used = {t["reply_id"] for t in call.get("dialog") or [] if t.get("reply_id")}
    return DialogContext(
        scenario=officer,
        history=turns_of(call),
        conversation_id=f"{attempt.id}:{call['id']}",
        used_reply_ids=used,
        role=ROLE_OFFICER,
    )


async def _event(
    session: AsyncSession, attempt: Attempt, type_: str, call: dict, **extra: object
) -> SessionEvent:
    return await append_event(
        session,
        session_id=attempt.session_id,
        type_=type_,
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "service_call_id": call["id"],
            "service": call["service"],
            "answered": call.get("answered", False),
            "ended_at": call.get("ended_at"),
            "end_reason": call.get("end_reason"),
            "facts_passed": list(call.get("facts_passed") or []),
            **extra,
        },
    )


# ---------------------------------------------------------------- lifecycle


async def start(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CardResponseScenario,
    service: str,
    service_title: str,
    *,
    telephony: bool,
) -> tuple[dict, list[SessionEvent]]:
    """«Позвонить»: a new call record. Without telephony the officer answers at once and the
    greeting is turn 0; with telephony the record waits for the SIP leg (``answer``)."""
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Работа с карточкой завершена: звонить уже нельзя.")
    current = open_call(attempt)
    if current is not None:
        raise ApiError(
            409,
            "call_in_progress",
            "Уже идёт разговор со службой: завершите его, прежде чем звонить снова.",
        )
    now = training.utcnow()
    events: list[SessionEvent] = []
    if attempt.state == ATTEMPT_ISSUED:
        # Calling from a card not opened yet still means it was received.
        events += await training.open_attempt(session, attempt, ts)
    call = {
        "id": uuid.uuid4().hex,
        "service": service,
        "service_title": service_title,
        "started_at": _iso(now),
        "answered": False,
        "answered_at": None,
        "ended_at": None,
        "end_reason": None,
        "telephony": telephony,
        "recording_path": None,
        "dialog": [],
        "facts_passed": [],
    }
    attempt.service_calls = [*calls_of(attempt), call]
    flag_modified(attempt, "service_calls")
    events.append(await _event(session, attempt, EVENT_STARTED, call))
    if not telephony:
        answered = await answer(session, attempt, ts, version, scenario, call["id"], now=now)
        events.extend(answered[1])
        call = answered[0]
    await session.flush()
    return call, events


async def answer(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CardResponseScenario,
    call_id: str,
    *,
    now: datetime | None = None,
) -> tuple[dict, list[SessionEvent]]:
    """The officer picked up: the greeting becomes turn 0 (voiced when a voice is available).
    Idempotent."""
    call = find_call(attempt, call_id)
    if call.get("ended_at"):
        raise ApiError(409, "call_ended", "Звонок уже завершён.")
    if call.get("answered"):
        return call, []
    now = now or training.utcnow()
    officer = officer_scenario(scenario, call["service"], call.get("service_title") or "")
    audio = await _greeting_audio(version, officer, call["service"])
    call = {
        **call,
        "answered": True,
        "answered_at": _iso(now),
        "dialog": [
            _turn(
                "caller", officer.caller.opening, ["greeting"], now, method="opening", audio=audio
            )
        ],
    }
    _store(attempt, call)
    if attempt.state in (ATTEMPT_ISSUED, ATTEMPT_RECEIVED):
        attempt.state = ATTEMPT_IN_PROGRESS
    return call, [await _event(session, attempt, EVENT_ANSWERED, call)]


async def end(
    session: AsyncSession, attempt: Attempt, call_id: str, reason: str
) -> tuple[dict, list[SessionEvent]]:
    """Ends the call once; later calls with another reason are ignored."""
    call = find_call(attempt, call_id)
    if call.get("ended_at"):
        return call, []
    call = {**call, "ended_at": _iso(training.utcnow()), "end_reason": reason}
    _store(attempt, call)
    return call, [await _event(session, attempt, EVENT_ENDED, call, reason=reason)]


async def end_open_calls(
    session: AsyncSession, attempt: Attempt, reason: str = END_CARD_CLOSED
) -> list[SessionEvent]:
    """Closing the card ends a call still in progress (the transcript stays)."""
    events: list[SessionEvent] = []
    current = open_call(attempt)
    while current is not None:
        _, ended = await end(session, attempt, current["id"], reason)
        events.extend(ended)
        current = open_call(attempt)
    return events


def set_recording(attempt: Attempt, call_id: str, path: str) -> None:
    call = find_call(attempt, call_id)
    _store(attempt, {**call, "recording_path": path})


# ---------------------------------------------------------------- turns


def _find_retry(call: dict, action_id: str | None) -> OfficerTurn | None:
    if not action_id:
        return None
    turns = call.get("dialog") or []
    for i in range(len(turns) - 1, max(len(turns) - 2 * _RETRY_WINDOW, -1), -1):
        if turns[i].get("role") == "operator" and turns[i].get("action_id") == action_id:
            officer = turns[i + 1] if i + 1 < len(turns) else turns[i]
            return OfficerTurn(
                turns[i],
                officer,
                applied=False,
                pending_reply=False,
                latency_ms=0,
                facts_passed=list(call.get("facts_passed") or []),
            )
    return None


async def say(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    version: ScenarioVersion,
    scenario: CardResponseScenario,
    call_id: str,
    text: str,
    *,
    action_id: str | None = None,
    heard: bool = False,
) -> OfficerTurn:
    """The dispatcher's phrase and the officer's answer."""
    call = find_call(attempt, call_id)
    if call.get("ended_at"):
        raise ApiError(409, "call_ended", "Звонок завершён: дежурному больше не сказать.")
    if not call.get("answered"):
        raise ApiError(409, "not_answered", "Дежурный ещё не ответил.")
    retry = _find_retry(call, action_id)
    if retry is not None:
        return retry
    now = training.utcnow()
    officer = officer_scenario(scenario, call["service"], call.get("service_title") or "")
    reply = await dialog.provider_for(ts).reply(_context(attempt, call, officer), text)
    operator = _turn("operator", text, reply.operator_topics, now, action_id=action_id, heard=heard)
    audio = await dialog.reply_audio(None, version, officer, reply, stem=_reply_stem(call, reply))
    officer_turn = _turn(
        "caller",
        reply.text,
        reply.topics,
        now,
        reply_id=reply.reply_id,
        method=reply.method,
        audio=audio,
        generated=reply.generated,
    )
    turns = [*(call.get("dialog") or []), operator, officer_turn]
    facts = engine.facts_from_dialog(
        scenario.card,
        [
            DialogTurn(role=t["role"], text=t["text"], topics=list(t.get("topics") or []))
            for t in turns
        ],
    )
    call = {**call, "dialog": turns, "facts_passed": facts}
    _store(attempt, call)
    pending = False
    if reply.generated:
        pending = _add_pending_reply(version, call["service"], reply)
    await session.flush()
    event = await _event(
        session,
        attempt,
        EVENT_TURN,
        call,
        turns=len(turns),
        operator=operator,
        officer=officer_turn,
        pending_reply=pending,
    )
    return OfficerTurn(
        operator,
        officer_turn,
        applied=True,
        pending_reply=pending,
        latency_ms=reply.latency_ms,
        facts_passed=facts,
        events=[event],
    )


def _reply_stem(call: dict, reply: CallerReply) -> str:
    if reply.reply_id:
        return f"officer-{call['service']}-r{reply.reply_id}"
    return f"officer-{call['id'][:8]}-{len(call.get('dialog') or [])}"


async def _greeting_audio(
    version: ScenarioVersion, officer: CallIntakeScenario, service: str
) -> str | None:
    reply = CallerReply(text=officer.caller.opening, topics=["greeting"], operator_topics=[])
    return await dialog.reply_audio(
        None, version, officer, reply, stem=f"officer-{service}-greeting"
    )


def _add_pending_reply(version: ScenarioVersion, service: str, reply: CallerReply) -> bool:
    """hybrid / generate: a new officer reply goes into the scenario «на утверждение»
    (``service_replies``, ``approved: false``)."""
    replies = list(version.body.get("service_replies") or [])
    next_id = max((int(r.get("id", 0)) for r in replies), default=0) + 1
    replies.append(
        {
            "id": next_id,
            "topic": reply.topics[0] if reply.topics else "unknown",
            "text": reply.text,
            "audio": None,
            "approved": False,
            "service": service,
            "source": "generated",
        }
    )
    version.body = {**version.body, "service_replies": replies}
    flag_modified(version, "body")
    return True
