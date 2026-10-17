"""Calls of the dispatcher to a service officer (issue #36, «звено Б → В») and the squad's
reports back to the dispatcher (customer, 21.09.2026).

A card-response attempt may hold several such calls (``attempts.service_calls``), one per
«Позвонить» in the services strip. Each call is a small dialog: the officer greets (turn 0), the
dispatcher passes the facts, the officer confirms or asks. The officer is played by the same
``DialogProvider`` as the caller of a 112 call, on a synthetic caller scenario built from the
card (``app.domain.scenarios.officers``), with the officer prompts and topics
(``DialogContext.role``). Facts passed are counted after every turn for the panel; the
evaluation engine recounts them from the stored turns.

A report (``kind: "report"``) is the same record started by the system on the squad's
timeline (``training.service.sweep_reports``): turn 0 is the squad leader's report instead of
a greeting, ``report_status`` names the status it stands for, and the leader answers from a
small set of confirmations. Facts are not counted on reports. A report is an incoming call:
the record waits for the trainee to answer it in both modes (issue #103), and one nobody
answered ends as ``not_taken``.

Rows of ``attempts.service_calls``::

    {id, service, kind, report_status, started_at, answered, answered_at, ended_at,
     end_reason, telephony, recording_path, dialog: [turns as in attempts.dialog],
     facts_passed}

Every function works inside the caller's transaction and returns the events it appended.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.dialog import service as dialog
from app.domain.evaluation import service_call as engine
from app.domain.evaluation.schemas import (
    BrigadeReport,
    CallIntakeScenario,
    CardResponseScenario,
    DialogTurn,
    ServiceCallRef,
)
from app.domain.evaluation.text import detect_topics
from app.domain.scenarios import (
    caller_back,
    complication,
    misheard,
    no_vehicles,
    officers,
    rush,
    staff,
)
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
from app.providers.dialog import (
    ROLE_CALLER,
    ROLE_OFFICER,
    CallerReply,
    DialogContext,
    detect_officer_topics,
)
from app.training import service as training

EVENT_STARTED = "service_call.started"
EVENT_ANSWERED = "service_call.answered"
EVENT_ENDED = "service_call.ended"
EVENT_TURN = "service_call.turn"
END_HANGUP = "hangup"  # the dispatcher ended the call
END_NO_ANSWER = "no_answer"
END_FAILED = "failed"
END_CARD_CLOSED = "card_closed"  # the card was closed with the call still open
END_NOT_TAKEN = "not_taken"  # a report the dispatcher never picked up / answered in time
KIND_OUTGOING = "outgoing"
KIND_REPORT = "report"
# Звонок диспетчера самому заявителю по номеру из карточки (ответ заказчика 23.09.2026).
# Собеседник не служба, поэтому у записи звонка служебный код-заглушка.
KIND_CALLER = "caller"
CALLER_TARGET = "caller"
# Звонок своему сотруднику по телефону (руководитель организации, начальник отдела
# реагирования; #263): не вызов бригады, факты карточки ему не «передаются» как службе.
KIND_STAFF = "staff"
# Studio recordings of the built-in banks in STORAGE_DIR (app.seed, issue #59): one folder
# per service for the duty officers, one shared for the squad leader — his answers are the
# same whatever the service.
STUDIO_OFFICERS_DIR = "tts/seed/_officers"
STUDIO_SQUAD_KEY = "_squad"
STUDIO_CALLER_KEY = "_caller"
STUDIO_STAFF_KEY = "_staff"
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


def is_report(call: dict) -> bool:
    return call.get("kind") == KIND_REPORT


def is_caller(call: dict) -> bool:
    """A call back to the person who reported the incident, not to a service."""
    return call.get("kind") == KIND_CALLER


def is_staff(call: dict) -> bool:
    """A call to a person of the own organization by the phone, not to a service."""
    return call.get("kind") == KIND_STAFF


def reports_of(attempt: Attempt) -> list[dict]:
    return [c for c in calls_of(attempt) if is_report(c)]


def delivered_reports(attempt: Attempt) -> list[str]:
    """Statuses the squad has reported so far (report calls started, in order)."""
    return [c["report_status"] for c in reports_of(attempt) if c.get("report_status")]


def squad_state(attempt: Attempt) -> str:
    """Where the squad is, one truth for every answer: no vehicles while the head has not
    found one (``no_vehicles``), else by the reports delivered."""
    if no_vehicles.blocked(calls_of(attempt)):
        return officers.SQUAD_STATE_NO_VEHICLES
    return officers.squad_state(delivered_reports(attempt))


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


def is_repeat(call: dict) -> bool:
    """The dispatcher calls a service he has already passed the card to (``start``)."""
    return bool(call.get("repeat"))


def card_passed_to(attempt: Attempt, service: str) -> bool:
    """An earlier call of the dispatcher to ``service`` passed at least one fact of the card."""
    return any(
        (c.get("kind") or KIND_OUTGOING) == KIND_OUTGOING
        and c.get("service") == service
        and c.get("facts_passed")
        for c in calls_of(attempt)
    )


def officer_scenario(
    scenario: CardResponseScenario,
    service: str,
    service_title: str,
    attempt: Attempt,
    *,
    repeat: bool = False,
) -> CallIntakeScenario:
    """The officer of ``service`` as he is now: the progress answer follows the squad, the
    built-in replies carry their studio recordings when the installation has them. On a
    repeat call the card is with him already (``officers.officer_scenario``). On a card of
    difficulty 3 the squad chief of the own service picks up in a hurry (``rush``)."""
    built = officers.officer_scenario(
        scenario,
        reference_for(scenario, service),
        service_title,
        squad_state(attempt),
        repeat=repeat,
        chief=service == scenario.service,
    )
    if service == scenario.service and not repeat and rush.applies(scenario.difficulty):
        hurried = built.caller.model_copy(update={"opening": rush.opening(built.caller.opening)})
        built = built.model_copy(update={"caller": hurried})
    return with_studio_audio(built, service)


def call_scenario(
    scenario: CardResponseScenario, call: dict, attempt: Attempt
) -> CallIntakeScenario:
    """The other side of ``call``: the person who reported the incident, the squad leader
    with his report, or the duty officer of a service."""
    title = call.get("service_title") or ""
    if is_caller(call):
        # Заявитель видит то же, что и диспетчер: выехала бригада или уже работает на месте.
        arrived = squad_state(attempt) in officers.SQUAD_STATES_ON_SCENE
        built = caller_back.callback_scenario(scenario, arrived=arrived)
        # Записи заявителя лежат по голосам: одна и та же фраза мужским и женским голосом
        # это два разных файла.
        return with_studio_audio(built, f"{STUDIO_CALLER_KEY}/{built.caller.voice}")
    if is_staff(call):
        role = call.get(no_vehicles.ROLE_KEY) or staff.ROLE_HEAD
        org = officers.spoken_service(scenario.service, title)
        state = squad_state(attempt)
        progress = (
            staff.SPECIALIST_PROGRESS.get(state, "")
            if role == staff.ROLE_SQUAD_SPECIALIST
            else officers.PROGRESS_REPLIES[state]
        )
        person = staff.person_scenario(role, scenario, org, progress)
        return with_studio_audio(person, f"{STUDIO_STAFF_KEY}/{role}")
    if is_report(call):
        report = _report_for(scenario, call, attempt)
        leader = officers.report_scenario(scenario, report, title)
        if report.status == complication.STATUS:
            return with_studio_audio(complication.specialist(leader), complication.STUDIO_KEY)
        return with_studio_audio(leader, STUDIO_SQUAD_KEY)
    return officer_scenario(scenario, call["service"], title, attempt, repeat=is_repeat(call))


def _report_of(scenario: CardResponseScenario, call: dict) -> BrigadeReport:
    for report in scenario.reference.reports:
        if report.status == call.get("report_status"):
            return report
    # The scenario changed after the call was made: keep what the record says.
    return BrigadeReport(
        status=call.get("report_status") or "response_started", text=call.get("opening") or ""
    )


def _report_for(scenario: CardResponseScenario, call: dict, attempt: Attempt) -> BrigadeReport:
    """The report as the squad says it now: a squad sent to a misheard house names that house
    (``misheard``), a report worded before #263 calls him by the customer's title, and a
    recording that no longer matches the words is dropped."""
    report = _report_of(scenario, call)
    text = officers.renamed(report.text)
    text = misheard.report_text(text, scenario.card, misheard.state(calls_of(attempt)))
    if text == report.text:
        return report
    return report.model_copy(update={"text": text, "audio": None})


def passes_facts(call: dict) -> bool:
    """Facts are passed to a duty officer only: a report brings them, and a call back to the
    caller of the card is not about passing anything (ответ заказчика 23.09.2026), and a
    report to the own head is not a call to a service (#263)."""
    return not is_report(call) and not is_caller(call) and not is_staff(call)


def _context(attempt: Attempt, call: dict, officer: CallIntakeScenario) -> DialogContext:
    used = {t["reply_id"] for t in call.get("dialog") or [] if t.get("reply_id")}
    return DialogContext(
        scenario=officer,
        history=turns_of(call),
        conversation_id=f"{attempt.id}:{call['id']}",
        used_reply_ids=used,
        # На обратном звонке говорит заявитель, значит и подсказки, и словарь тем — его.
        role=ROLE_CALLER if is_caller(call) else ROLE_OFFICER,
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
            "kind": call.get("kind") or KIND_OUTGOING,
            "report_status": call.get("report_status"),
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
    answer_now: bool = True,
    kind: str = KIND_OUTGOING,
    role: str | None = None,
) -> tuple[dict, list[SessionEvent]]:
    """«Позвонить»: a new call record. Without telephony the other side answers at once and
    the greeting is turn 0; with telephony the record waits for the SIP leg (``answer``). In a
    cloud lesson the browser call answers it instead (``answer_now=False``): the first phrase
    is said by the cloud, and writing our own greeting would double it. ``kind`` tells a call
    to a service officer from a call back to the caller of the card (``KIND_CALLER``) and
    from a call to a person of the own organization (``KIND_STAFF`` with ``role``). The first
    call to the squad chief of a card of difficulty 3 fixes its turn (``no_vehicles``)."""
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Работа с карточкой завершена: звонить уже нельзя.")
    current = open_call(attempt)
    if current is not None:
        raise ApiError(
            409,
            "call_in_progress",
            "Уже идёт разговор: завершите его, прежде чем звонить снова.",
        )
    now = training.utcnow()
    events: list[SessionEvent] = []
    if attempt.state == ATTEMPT_ISSUED:
        # Calling from a card not opened yet still means it was received.
        events += await training.open_attempt(session, attempt, ts)
    call = _new_call(service, service_title, now, telephony=telephony, kind=kind)
    if kind == KIND_OUTGOING and card_passed_to(attempt, service):
        call["repeat"] = True
    if kind == KIND_STAFF:
        call[no_vehicles.ROLE_KEY] = role
    if kind == KIND_OUTGOING and service == scenario.service:
        plan = await _squad_plan(session, attempt, scenario)
        if plan is not None:
            call[no_vehicles.PLAN_KEY] = plan
    attempt.service_calls = [*calls_of(attempt), call]
    flag_modified(attempt, "service_calls")
    events.append(await _event(session, attempt, EVENT_STARTED, call))
    if not telephony and answer_now:
        answered = await answer(session, attempt, ts, version, scenario, call["id"], now=now)
        events.extend(answered[1])
        call = answered[0]
    await session.flush()
    return call, events


async def _squad_plan(
    session: AsyncSession, attempt: Attempt, scenario: CardResponseScenario
) -> str | None:
    """The turn of a card of difficulty 3, once per card: «машин нет» or a complication,
    alternating over the trainee's cards of the lesson, «машин нет» first."""
    if not no_vehicles.applies(scenario.difficulty):
        return None
    if no_vehicles.plan_of(calls_of(attempt)) is not None:
        return None
    earlier = await session.scalars(
        select(Attempt.service_calls)
        .where(
            Attempt.session_id == attempt.session_id,
            Attempt.student_id == attempt.student_id,
            Attempt.id != attempt.id,
            Attempt.issued_at <= attempt.issued_at,
        )
        .order_by(Attempt.issued_at.desc())
    )
    previous = next((p for calls in earlier if (p := no_vehicles.plan_of(calls or []))), None)
    return no_vehicles.next_plan(previous)


def _new_call(
    service: str,
    service_title: str,
    now: datetime,
    *,
    telephony: bool,
    kind: str = KIND_OUTGOING,
) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "service": service,
        "service_title": service_title,
        "kind": kind,
        "report_status": None,
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


async def start_report(
    session: AsyncSession,
    attempt: Attempt,
    scenario: CardResponseScenario,
    report: BrigadeReport,
    service_title: str,
    *,
    telephony: bool,
    now: datetime | None = None,
) -> tuple[dict, list[SessionEvent]]:
    """The squad leader calls the dispatcher with ``report``: a new record of kind «report».
    The record always waits for the trainee to pick up (``answer``), by the phone with
    telephony and by the button in the card without it (issue #103). The caller makes sure no
    other call is open and the report was not delivered yet."""
    if attempt.state in training.CLOSED_STATES:
        raise ApiError(409, "card_closed", "Работа с карточкой завершена.")
    if open_call(attempt) is not None:
        raise ApiError(409, "call_in_progress", "Уже идёт разговор.")
    now = now or training.utcnow()
    opening = misheard.report_text(
        officers.renamed(report.text), scenario.card, misheard.state(calls_of(attempt))
    )
    call = {
        **_new_call(scenario.service, service_title, now, telephony=telephony),
        "kind": KIND_REPORT,
        "report_status": report.status,
        "opening": opening,
    }
    attempt.service_calls = [*calls_of(attempt), call]
    flag_modified(attempt, "service_calls")
    events = [await _event(session, attempt, EVENT_STARTED, call)]
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
    with_opening: bool = True,
) -> tuple[dict, list[SessionEvent]]:
    """The officer picked up: the greeting becomes turn 0 (voiced whenever a voice is
    available). In a cloud call the first phrase is said by the cloud itself, so
    ``with_opening=False`` leaves the transcript to the provider. Idempotent."""
    call = find_call(attempt, call_id)
    if call.get("ended_at"):
        raise ApiError(409, "call_ended", "Звонок уже завершён.")
    if call.get("answered"):
        return call, []
    now = now or training.utcnow()
    officer = call_scenario(scenario, call, attempt)
    # В облачном звонке первую фразу говорит сам провайдер: своей реплики не пишем и не озвучиваем.
    if not with_opening:
        audio, topics = None, []
    elif is_report(call):
        audio = await _report_audio(version, officer, _report_for(scenario, call, attempt))
        topics = ["report", call.get("report_status") or ""]
    else:
        # A call back uses the caller's own folder of recordings, split by voice.
        key = f"{STUDIO_CALLER_KEY}/{officer.caller.voice}" if is_caller(call) else call["service"]
        if is_staff(call):
            key = f"{STUDIO_STAFF_KEY}/{call.get(no_vehicles.ROLE_KEY)}"
        audio = await _greeting_audio(version, officer, key, repeat=is_repeat(call))
        topics = ["greeting"]
    opening = (
        [
            _turn(
                "caller",
                officer.caller.opening,
                [t for t in topics if t],
                now,
                method="opening",
                audio=audio,
            )
        ]
        if with_opening
        else []
    )
    call = {**call, "answered": True, "answered_at": _iso(now), "dialog": opening}
    _store(attempt, call)
    if attempt.state in (ATTEMPT_ISSUED, ATTEMPT_RECEIVED):
        attempt.state = ATTEMPT_IN_PROGRESS
    if with_opening:  # a cloud call is played by the provider, not by our model
        dialog.warm_up(ts, _context(attempt, call, officer))
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


async def external_turn(
    session: AsyncSession,
    attempt: Attempt,
    call_id: str,
    scenario: CardResponseScenario,
    role: str,
    text: str,
    *,
    now: datetime | None = None,
    latency_ms: int | None = None,
) -> tuple[dict | None, list[SessionEvent]]:
    """One phrase of a service call heard from the cloud voice (issue #59, browser calls):
    the dispatcher's own words as the provider transcribed them, or the reply of the officer
    (the squad leader on a report). Topics come from keywords, so the evaluation reads the
    turn like any other. ``role`` is «operator» (the trainee) or «caller» (the other side)."""
    if role not in ("operator", "caller"):
        raise ValueError(f"unknown dialog role: {role!r}")
    text = text.strip()
    if not text:
        return None, []
    call = find_call(attempt, call_id)
    if call.get("ended_at"):
        return None, []
    now = now or training.utcnow()
    if role != "operator":
        topics = []
    elif is_caller(call):
        topics = detect_topics(text)
    else:
        topics = detect_officer_topics(text)
    turn = _turn(
        role,
        text,
        topics,
        now,
        method=dialog.EXTERNAL_METHOD,
        heard=role == "operator",
        latency_ms=latency_ms if role == "caller" else None,
    )
    turns = [*(call.get("dialog") or []), turn]
    facts = list(call.get("facts_passed") or [])
    if passes_facts(call):
        facts = engine.facts_from_dialog(
            scenario.card,
            [
                DialogTurn(role=t["role"], text=t["text"], topics=list(t.get("topics") or []))
                for t in turns
            ],
        )
    call = {**call, "dialog": turns, "facts_passed": facts, "answered": True}
    if not call.get("answered_at"):
        call["answered_at"] = _iso(now)
    _store(attempt, call)
    if attempt.state in (ATTEMPT_ISSUED, ATTEMPT_RECEIVED):
        attempt.state = ATTEMPT_IN_PROGRESS
    await session.flush()
    event = await _event(
        session,
        attempt,
        EVENT_TURN,
        call,
        turns=len(turns),
        operator=turn if role == "operator" else None,
        officer=turn if role == "caller" else None,
        pending_reply=False,
    )
    return turn, [event]


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
    """The dispatcher's phrase and the answer of the other side."""
    call = find_call(attempt, call_id)
    caller_side = is_caller(call)
    if call.get("ended_at"):
        whom = "заявителю" if caller_side else "дежурному"
        raise ApiError(409, "call_ended", f"Звонок завершён: {whom} больше не сказать.")
    if not call.get("answered"):
        if is_report(call):
            message = "Вы ещё не ответили на звонок: нажмите «Ответить»."
        else:
            message = "Заявитель ещё не ответил." if caller_side else "Дежурный ещё не ответил."
        raise ApiError(409, "not_answered", message)
    retry = _find_retry(call, action_id)
    if retry is not None:
        return retry
    now = training.utcnow()
    officer = call_scenario(scenario, call, attempt)
    special = special_reply(scenario, attempt, call, text)
    if special is not None:
        reply, extra = special
    else:
        reply = await dialog.provider_for(ts).reply(_context(attempt, call, officer), text)
        extra = {}
    reply = no_vehicles_reply(scenario, attempt, call, text, reply)
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
        **extra,
    )
    turns = [*(call.get("dialog") or []), operator, officer_turn]
    facts: list[str] = []
    if passes_facts(call):
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
    if reply.generated and passes_facts(call):
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


def special_reply(
    scenario: CardResponseScenario, attempt: Attempt, call: dict, text: str
) -> tuple[CallerReply, dict] | None:
    """The scripted moves of the squad chief that the dialog provider does not make: cutting a
    long phrase short (``rush``), mishearing the house (``misheard``). ``None`` leaves the
    phrase to the provider; the fields of the officer's turn come second."""
    return (
        rush_reply(scenario, call, text)
        or misheard_reply(scenario, attempt, call, text)
        or head_reply(attempt, call, text)
        or wrong_contact_reply(call, text)
    )


def head_reply(attempt: Attempt, call: dict, text: str) -> tuple[CallerReply, dict] | None:
    """The head of the organization hears that there are no vehicles and finds one."""
    if not is_staff(call) or call.get(no_vehicles.ROLE_KEY) != staff.ROLE_HEAD:
        return None
    if not no_vehicles.blocked(calls_of(attempt)) or not no_vehicles.mentions_shortage(text):
        return None
    reply = CallerReply(
        text=no_vehicles.RESOLVED,
        topics=[no_vehicles.TOPIC_VEHICLES_FOUND],
        operator_topics=[],
        method="no_vehicles",
    )
    return reply, {}


def wrong_contact_reply(call: dict, text: str) -> tuple[CallerReply, dict] | None:
    """A person of the organization told to send the squad sends the dispatcher to its chief:
    only he gives the squad its order (#263)."""
    if not is_staff(call) or not engine.gives_order(text):
        return None
    reply = CallerReply(
        text=staff.WRONG_CONTACT,
        topics=[staff.TOPIC_WRONG_CONTACT],
        operator_topics=[],
        method="wrong_contact",
    )
    return reply, {}


def no_vehicles_reply(
    scenario: CardResponseScenario, attempt: Attempt, call: dict, text: str, reply: CallerReply
) -> CallerReply:
    """On a «машин нет» card the squad chief's answer, once the address is with him and no
    question about the house is open, becomes «машин нет»; while the head has found nothing,
    a confirmation or a word about the squad turns into «машин так и нет»."""
    if call.get("kind") != KIND_OUTGOING or call.get("service") != scenario.service:
        return reply
    calls = calls_of(attempt)
    if no_vehicles.plan_of(calls) != no_vehicles.PLAN_NO_VEHICLES:
        return reply
    if misheard.TOPIC_MISHEARD in reply.topics or rush.TOPIC_RUSH in reply.topics:
        return reply
    if no_vehicles.announced(calls):
        if not no_vehicles.blocked(calls):
            return reply
        if not ({"confirm", "order_number", "progress"} & set(reply.topics)):
            return reply
        said = no_vehicles.STILL_NONE
    else:
        phrases = [text] + [
            t["text"] for t in call.get("dialog") or [] if t.get("role") == "operator"
        ]
        if not any(engine.address_named(scenario.card, p) for p in phrases):
            return reply
        # The house just corrected stays acknowledged: «Понял, дом 42… Только машин нет».
        fixed = reply.method == "misheard" and "confirm" in reply.topics
        said = f"{reply.text} {no_vehicles.SHORTAGE}" if fixed else no_vehicles.ANNOUNCE
    return CallerReply(
        text=said,
        topics=[no_vehicles.TOPIC_NO_VEHICLES],
        operator_topics=reply.operator_topics,
        method="no_vehicles",
    )


def rush_reply(
    scenario: CardResponseScenario, call: dict, text: str
) -> tuple[CallerReply, dict] | None:
    """On difficulty 3 the squad chief cuts a long phrase short, once per call (``rush``)."""
    if is_caller(call) or is_report(call) or is_repeat(call):
        return None
    if call.get("service") != scenario.service or not rush.applies(scenario.difficulty):
        return None
    if not rush.is_long(text) or rush.cut_in_already(call.get("dialog") or []):
        return None
    reply = CallerReply(
        text=rush.CUT_IN,
        topics=[rush.TOPIC_RUSH],
        operator_topics=detect_officer_topics(text),
        method="rush",
    )
    return reply, {}


def misheard_reply(
    scenario: CardResponseScenario, attempt: Attempt, call: dict, text: str
) -> tuple[CallerReply, dict] | None:
    """The squad chief mishears the house, or takes the correction (``misheard``); ``None``
    leaves the phrase to the dialog provider. The fields of the officer's turn come second.

    * The first time the dispatcher names the address to their own service (not a repeat
      call) on a card of difficulty 2 and up, the officer repeats it with a wrong house.
    * The house of the card named after that, in this call or any later one with the
      squad, corrects him.
    * Right after his question a bare «нет» gets «Какой дом тогда?», a bare «да» is taken.
    """
    if is_caller(call) or call.get("service") != scenario.service:
        return None
    card = scenario.card
    heard = detect_officer_topics(text)
    current = misheard.state(calls_of(attempt))

    def answer(said: str, topics: list[str], operator_topics: list[str]) -> CallerReply:
        return CallerReply(
            text=said, topics=topics, operator_topics=operator_topics, method="misheard"
        )

    if current is None:
        if is_report(call) or is_repeat(call):
            return None
        if not misheard.applies(scenario.difficulty, card):
            return None
        if not engine.address_named(card, text):
            return None
        said = misheard.wrong_house(card.address.house)
        reply = answer(misheard.question(card), [misheard.TOPIC_MISHEARD], heard)
        return reply, {misheard.HOUSE_KEY: said}
    if not current.squad_has_wrong_house:
        return None
    if misheard.names_house(card, text):
        squad_out = call["id"] != current.call_id
        said = misheard.fixed_reply(card, squad_out=squad_out)
        return answer(said, ["confirm"], [*heard, misheard.TOPIC_FIXED]), {}
    officer_turns = [t for t in call.get("dialog") or [] if t.get("role") == "caller"]
    if not officer_turns or misheard.TOPIC_MISHEARD not in (officer_turns[-1].get("topics") or []):
        return None
    if misheard.denies(text):
        return answer(misheard.ASK_HOUSE, [misheard.TOPIC_MISHEARD], heard), {}
    if misheard.agrees(text) and not heard:
        return answer(misheard.AGREED, ["confirm"], heard), {}
    return None


def _text_key(text: str) -> str:
    """Short digest of a phrase: the voice file of an edited text is a different file."""
    return hashlib.sha1(text.encode()).hexdigest()[:8]  # noqa: S324 - a cache key, not security


def studio_audio(service: str, text: str) -> str | None:
    """Studio recording of a built-in phrase of a service's officer (issue #59), when the
    installation carries one (``scripts/voice_replies.py`` → ``app.seed``). Without it the
    stand synthesizes the phrase as before."""
    relative = f"{STUDIO_OFFICERS_DIR}/{service}/{_text_key(text)}.mp3"
    return relative if (dialog.storage_root() / relative).is_file() else None


def with_studio_audio(scenario: CallIntakeScenario, service: str) -> CallIntakeScenario:
    """The officer's replies with their recordings: a reply that has none keeps ``audio``
    empty and is voiced by the stand."""
    replies = [
        r if r.audio else r.model_copy(update={"audio": studio_audio(service, r.text)})
        for r in scenario.replies
    ]
    return scenario.model_copy(update={"replies": replies})


def _reply_stem(call: dict, reply: CallerReply) -> str:
    if is_caller(call):
        # The caller of the card: his answers are his own, so cache them per attempt call.
        if reply.reply_id:
            return f"caller-back-r{reply.reply_id}-{_text_key(reply.text)}"
        return f"caller-back-{_text_key(reply.text)}"
    if is_report(call):
        # Report replies quote the report itself: cache per phrase, so every trainee who
        # hears the same squad leader gets the same file instead of a fresh synthesis.
        return f"report-{call.get('report_status') or 'report'}-{_text_key(reply.text)}"
    if is_staff(call):
        # Some replies say where the squad is: cache per phrase, not per reply.
        return f"staff-{call.get(no_vehicles.ROLE_KEY)}-{_text_key(reply.text)}"
    if reply.reply_id:
        return f"officer-{call['service']}-r{reply.reply_id}"
    return f"officer-{call['service']}-{_text_key(reply.text)}"


async def _report_audio(
    version: ScenarioVersion, leader: CallIntakeScenario, report: BrigadeReport
) -> str | None:
    """The squad leader's report: the recording of the scenario when it has one, else a
    synthesis cached per scenario version and text (issue #67)."""
    reply = CallerReply(
        text=leader.caller.opening, topics=["report"], operator_topics=[], audio=report.audio
    )
    stem = f"report-{report.status}-{_text_key(report.text)}"
    return await dialog.reply_audio(None, version, leader, reply, stem=stem)


async def _greeting_audio(
    version: ScenarioVersion, officer: CallIntakeScenario, service: str, *, repeat: bool = False
) -> str | None:
    opening = officer.caller.opening
    # The repeat call opens with where the squad is: its own file per phrase, so the cached
    # greeting of the first call is neither played here nor overwritten.
    if service.startswith(STUDIO_STAFF_KEY):
        # A person of the organization: the greeting may name where the squad is.
        stem = f"staff-{service.rsplit('/', 1)[-1]}-{_text_key(opening)}"
    elif repeat:
        stem = f"officer-{service}-repeat-{_text_key(opening)}"
    elif opening.endswith(rush.OPENING_TAIL):
        # The hurried greeting of difficulty 3 is its own file next to the calm one.
        stem = f"officer-{service}-greeting-{_text_key(opening)}"
    else:
        stem = f"officer-{service}-greeting"
    reply = CallerReply(
        text=opening,
        topics=["greeting"],
        operator_topics=[],
        audio=studio_audio(service, opening),
    )
    return await dialog.reply_audio(None, version, officer, reply, stem=stem)


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
