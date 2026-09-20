"""Card-response training: issuing cards to a trainee, opening them, setting response
statuses through the status machine, closing cards and the «Не оповещено» sweep.

Every function works inside the caller's transaction and returns the events it appended;
the router commits and then publishes them (see ``app.events``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.analytics import ratings
from app.domain.analytics.adaptive import order_queue
from app.domain.evaluation import evaluate_attempt
from app.domain.evaluation import status_machine as machine
from app.domain.evaluation.data_check import FIELD_TITLES
from app.errors import ApiError
from app.events import append_event
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    ATTEMPT_EVALUATED,
    ATTEMPT_FINISHED,
    ATTEMPT_IN_PROGRESS,
    ATTEMPT_ISSUED,
    ATTEMPT_RECEIVED,
    MODE_CALL_INTAKE,
    SCENARIO_APPROVED,
    SESSION_RUNNING,
    Attempt,
    Evaluation,
    GroupMember,
    RejectReason,
    Role,
    Scenario,
    ScenarioVersion,
    Service,
    SessionEvent,
    TrainingSession,
    User,
)

# Card statuses (codes of the ``card_statuses`` table) the trainer derives itself.
CARD_REGISTERED = "registered"
CARD_NOT_NOTIFIED = "not_notified"
CARD_REFUSED = "refused"
CARD_NOT_FINISHED = "not_finished"
CARD_FINISHED = "finished"

# How many cards are in the journal at once per session difficulty (PRD 9.2: at
# difficulty 3 the trainee handles several cards in parallel).
CARDS_AT_ONCE = {1: 1, 2: 1, 3: 3}

# Journal numbers look like real АРМ-112 incident numbers (8 digits).
_CARD_NUMBER_BASE = 38260000

# Who wrote a status_log entry.
BY_SYSTEM = "system"
BY_DISPATCHER = "dispatcher"


STATUS_ADDED = machine.ADDED
STATUS_RECEIVED = machine.RECEIVED
STATUS_REJECTED = machine.REJECTED
STATUS_WORKS_DONE = machine.WORKS_DONE
STATUS_WORKS_REFUSED = machine.WORKS_REFUSED

# Attempt states after which the card can no longer be edited.
CLOSED_STATES = (ATTEMPT_FINISHED, ATTEMPT_EVALUATED)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Transition:
    """One option of the status drop-down (memo page 24)."""

    code: str
    title: str
    requires_comment: bool
    requires_order_number: bool
    is_final: bool
    is_primary: bool


def status_title(code: str) -> str:
    return machine.title(code)


async def load_services(session: AsyncSession) -> dict[str, Service]:
    return {s.code: s for s in await session.scalars(select(Service))}


async def load_reject_reasons(session: AsyncSession) -> dict[str, RejectReason]:
    return {r.code: r for r in await session.scalars(select(RejectReason))}


# ---------------------------------------------------------------- access


async def student_group_ids(session: AsyncSession, student_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        await session.scalars(
            select(GroupMember.group_id).where(GroupMember.student_id == student_id)
        )
    )


async def sessions_for_student(session: AsyncSession, student: User) -> list[TrainingSession]:
    groups = await student_group_ids(session, student.id)
    if not groups:
        return []
    rows = await session.scalars(
        select(TrainingSession)
        .where(TrainingSession.group_id.in_(groups))
        .order_by(TrainingSession.created_at.desc())
    )
    return list(rows)


async def get_session_for(
    session: AsyncSession, session_id: uuid.UUID, user: User
) -> TrainingSession:
    """The training session if ``user`` takes part in it (student of the group or its
    teacher); otherwise 404 so foreign sessions are not enumerable."""
    ts = await session.get(TrainingSession, session_id)
    if ts is None:
        raise ApiError(404, "session_not_found", "Занятие не найдено.")
    if user.role == Role.teacher and ts.teacher_id == user.id:
        return ts
    if user.role == Role.student and ts.group_id in await student_group_ids(session, user.id):
        return ts
    raise ApiError(404, "session_not_found", "Занятие не найдено.")


async def get_attempt_for(session: AsyncSession, attempt_id: uuid.UUID, user: User) -> Attempt:
    """The attempt if ``user`` owns it or teaches its session; otherwise 404."""
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise ApiError(404, "attempt_not_found", "Карточка не найдена.")
    if user.role == Role.student and attempt.student_id == user.id:
        return attempt
    if user.role == Role.teacher:
        ts = await session.get(TrainingSession, attempt.session_id)
        if ts is not None and ts.teacher_id == user.id:
            return attempt
    raise ApiError(404, "attempt_not_found", "Карточка не найдена.")


# ---------------------------------------------------------------- scenarios


@dataclass
class ScenarioCard:
    scenario: Scenario
    version: ScenarioVersion

    @property
    def body(self) -> dict:
        return self.version.body

    @property
    def service_code(self) -> str | None:
        return self.body.get("service") or self.scenario.service_code


async def load_scenario_card(
    session: AsyncSession, scenario_id: uuid.UUID, version: int
) -> ScenarioCard:
    scenario = await session.get(Scenario, scenario_id)
    row = await session.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == scenario_id, ScenarioVersion.version == version
        )
    )
    if scenario is None or row is None:
        raise ApiError(500, "scenario_missing", "Сценарий карточки не найден в базе.")
    return ScenarioCard(scenario=scenario, version=row)


async def scenario_queue(session: AsyncSession, ts: TrainingSession) -> list[Scenario]:
    """Scenarios of the session in issue order."""
    if ts.scenario_ids:
        rows = {
            s.id: s
            for s in await session.scalars(select(Scenario).where(Scenario.id.in_(ts.scenario_ids)))
        }
        return [rows[i] for i in ts.scenario_ids if i in rows]
    query = (
        select(Scenario)
        .where(Scenario.kind == ts.mode, Scenario.status == SCENARIO_APPROVED)
        .order_by(Scenario.created_at, Scenario.title)
    )
    if ts.service_profile and ts.mode != MODE_CALL_INTAKE:
        query = query.where(Scenario.service_code.in_(ts.service_profile))
    rows = list(await session.scalars(query))
    if ts.incident_groups:
        rows = [
            s
            for s in rows
            if s.incident_type_code and s.incident_type_code.split(".")[0] in ts.incident_groups
        ]
    return rows


# ---------------------------------------------------------------- issuing cards


def _log_entry(
    status: str,
    *,
    at: datetime,
    by: str,
    order_number: str | None = None,
    comment: str | None = None,
    reject_reason: str | None = None,
    action_id: str | None = None,
) -> dict:
    return {
        "status": status,
        "order_number": order_number or None,
        "comment": comment or None,
        "reject_reason": reject_reason,
        "at": at.astimezone(UTC).isoformat(),
        "by": by,
        "action_id": action_id,
    }


async def _card_number(session: AsyncSession, ts: TrainingSession, scenario: Scenario) -> str:
    """The scenario's own number when it has one (duplicate scenarios refer to it), else a
    fresh number in the session."""
    card = await load_scenario_card(session, scenario.id, scenario.current_version)
    number = (card.body.get("card") or {}).get("number")
    if number:
        return str(number)
    count = await session.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.session_id == ts.id)
    )
    return str(_CARD_NUMBER_BASE + (count or 0) + 1)


def _attempt_event_payload(attempt: Attempt) -> dict:
    return {
        "attempt_id": attempt.id,
        "card_number": attempt.card_number,
        "state": attempt.state,
        "response_status": attempt.response_status,
        "card_status": attempt.card_status,
        "issued_at": attempt.issued_at,
        "received_at": attempt.received_at,
        "primary_status_at": attempt.primary_status_at,
        "submitted_at": attempt.submitted_at,
    }


async def issue_cards(
    session: AsyncSession, ts: TrainingSession, student: User
) -> tuple[list[Attempt], list[SessionEvent]]:
    """Puts the next cards of the queue into the trainee's journal so that
    ``CARDS_AT_ONCE[difficulty]`` cards are active. Called when the trainee asks for the
    journal (the norm of a card runs from «Добавлена», so a card is added only while the
    journal is in front of the trainee), never when a card closes. No-op when the session
    is not running or the queue is exhausted."""
    if ts.status != SESSION_RUNNING:
        return [], []
    active = await session.scalar(
        select(func.count())
        .select_from(Attempt)
        .where(
            Attempt.session_id == ts.id,
            Attempt.student_id == student.id,
            Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
        )
    )
    # The 112 operator takes one call at a time whatever the difficulty.
    at_once = 1 if ts.mode == MODE_CALL_INTAKE else CARDS_AT_ONCE.get(ts.difficulty, 1)
    want = at_once - (active or 0)
    if want <= 0:
        return [], []
    done = list(
        await session.scalars(
            select(Attempt.scenario_id).where(
                Attempt.session_id == ts.id, Attempt.student_id == student.id
            )
        )
    )
    if ts.cards_per_student:
        want = min(want, ts.cards_per_student - len(done))
        if want <= 0:
            return [], []
    candidates = [s for s in await scenario_queue(session, ts) if s.id not in set(done)]
    if ts.adaptive:
        # PRD 9.7: the weakest incident group first, difficulty near the rating.
        state = await ratings.load_state(session, student.id)
        candidates = order_queue(candidates, mode=ts.mode, ratings=state, done=done)
    queue = candidates[:want]
    attempts: list[Attempt] = []
    events: list[SessionEvent] = []
    for scenario in queue:
        now = utcnow()
        attempt = Attempt(
            session_id=ts.id,
            student_id=student.id,
            scenario_id=scenario.id,
            scenario_version=scenario.current_version,
            mode=ts.mode,
            card_number=await _card_number(session, ts, scenario),
            issued_at=now,
            status_log=[_log_entry(STATUS_ADDED, at=now, by=BY_SYSTEM)],
            state=ATTEMPT_ISSUED,
            response_status=STATUS_ADDED,
            card_status=CARD_REGISTERED,
        )
        session.add(attempt)
        await session.flush()
        attempts.append(attempt)
        events.append(
            await append_event(
                session,
                session_id=ts.id,
                type_="attempt.issued",
                student_id=student.id,
                payload={**_attempt_event_payload(attempt), "scenario_id": scenario.id},
            )
        )
    return attempts, events


# ---------------------------------------------------------------- trainee actions


async def open_attempt(
    session: AsyncSession, attempt: Attempt, ts: TrainingSession
) -> list[SessionEvent]:
    """«Получена службой»: the first time the dispatcher opens the card."""
    if attempt.state != ATTEMPT_ISSUED:
        return []
    now = utcnow()
    attempt.received_at = now
    attempt.state = ATTEMPT_RECEIVED
    attempt.response_status = STATUS_RECEIVED
    attempt.status_log = [*attempt.status_log, _log_entry(STATUS_RECEIVED, at=now, by=BY_SYSTEM)]
    flag_modified(attempt, "status_log")
    event = await append_event(
        session,
        session_id=ts.id,
        type_="attempt.received",
        student_id=attempt.student_id,
        payload=_attempt_event_payload(attempt),
    )
    return [event]


def derive_card_status(attempt: Attempt) -> str:
    """Card status shown in the journal (memo, pages 27-28), from the service's last status."""
    last = attempt.response_status
    if last == STATUS_WORKS_DONE:
        return CARD_FINISHED
    if last in (STATUS_REJECTED, STATUS_WORKS_REFUSED):
        return CARD_REFUSED
    if attempt.primary_status_at is None and attempt.card_status == CARD_NOT_NOTIFIED:
        return CARD_NOT_NOTIFIED
    if attempt.card_status == CARD_NOT_FINISHED:
        return CARD_NOT_FINISHED
    return CARD_REGISTERED


@dataclass
class StatusChange:
    changed: bool
    events: list[SessionEvent]
    issued: list[Attempt]


async def set_status(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    student: User,
    *,
    status: str,
    order_number: str | None,
    comment: str | None,
    reject_reason: str | None,
    action_id: str | None,
) -> StatusChange:
    """Sets a response status via the status machine. Idempotent by ``action_id``: a retry
    of an already applied action changes nothing and reports ``changed=False``."""
    if action_id and any(e.get("action_id") == action_id for e in attempt.status_log):
        return StatusChange(changed=False, events=[], issued=[])
    if attempt.state in CLOSED_STATES:
        raise ApiError(
            409, "card_closed", "Работа с карточкой завершена: изменить статус уже нельзя."
        )
    services = await load_services(session)
    card = await load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    service = services.get(card.service_code or "")
    spec = machine.STATUSES.get(status)
    if spec is not None and spec["is_system"]:
        raise ApiError(
            422,
            "not_allowed",
            f"Статус «{spec['title']}» проставляет система, выбрать его нельзя.",
        )
    try:
        machine.validate_transition(
            attempt.response_status,
            status,
            comment=comment,
            order_number=order_number,
            no_reject=bool(service and service.no_reject),
        )
    except machine.TransitionError as exc:
        raise ApiError(422, exc.code, exc.message) from exc
    rule = machine.STATUSES[status]
    if reject_reason is not None:
        reasons = await load_reject_reasons(session)
        if reject_reason not in reasons:
            raise ApiError(422, "unknown_reject_reason", "Такой причины отказа нет в списке.")

    now = utcnow()
    events: list[SessionEvent] = []
    if attempt.state == ATTEMPT_ISSUED:
        # Setting a status without opening the card first still means it was received.
        events += await open_attempt(session, attempt, ts)
    before_card_status = attempt.card_status
    attempt.status_log = [
        *attempt.status_log,
        _log_entry(
            status,
            at=now,
            by=BY_DISPATCHER,
            order_number=order_number,
            comment=comment,
            reject_reason=reject_reason,
            action_id=action_id,
        ),
    ]
    flag_modified(attempt, "status_log")
    attempt.response_status = status
    if rule["is_primary"] and attempt.primary_status_at is None:
        attempt.primary_status_at = now
    attempt.state = ATTEMPT_FINISHED if rule["is_final"] else ATTEMPT_IN_PROGRESS
    if rule["is_final"]:
        attempt.submitted_at = now
    attempt.card_status = derive_card_status(attempt)

    events.append(
        await append_event(
            session,
            session_id=ts.id,
            type_="attempt.status_changed",
            student_id=attempt.student_id,
            payload={
                **_attempt_event_payload(attempt),
                "status": status,
                "order_number": order_number or None,
                "comment": comment or None,
                "at": now,
            },
        )
    )
    if attempt.card_status != before_card_status:
        events.append(await _card_status_event(session, attempt))
    if rule["is_final"]:
        events.extend(await _end_service_calls(session, attempt))
        events.append(await _submitted_event(session, attempt))
        events.append(await evaluate_and_store(session, attempt, ts, card.body))
    # The next card is not issued here: its 30 s run from «Добавлена», so it appears when
    # the trainee comes back to the journal (docs/BUGS.md, 9), see ``journal``.
    return StatusChange(changed=True, events=events, issued=[])


async def flag_field(
    session: AsyncSession,
    attempt: Attempt,
    ts: TrainingSession,
    *,
    field: str,
    corrected_value: str,
    action_id: str | None,
) -> StatusChange:
    """«Отметить ошибку»: the dispatcher marks a field of the card as an operator mistake and
    names the right value (issue #35). One flag per field: a second flag replaces the value.
    Idempotent by ``action_id``. Allowed until the card is closed."""
    if action_id and any(f.get("action_id") == action_id for f in attempt.flagged_fields):
        return StatusChange(changed=False, events=[], issued=[])
    if attempt.state in CLOSED_STATES:
        raise ApiError(
            409, "card_closed", "Работа с карточкой завершена: отметить ошибку уже нельзя."
        )
    if field not in FIELD_TITLES:
        raise ApiError(422, "unknown_field", f"В карточке нет поля «{field}».")
    value = corrected_value.strip()
    if not value:
        raise ApiError(422, "empty_value", "Укажите, каким должно быть верное значение поля.")
    events: list[SessionEvent] = []
    if attempt.state == ATTEMPT_ISSUED:
        events += await open_attempt(session, attempt, ts)
    now = utcnow()
    kept = [f for f in attempt.flagged_fields if f.get("field") != field]
    attempt.flagged_fields = [
        *kept,
        {"field": field, "corrected_value": value, "at": now.isoformat(), "action_id": action_id},
    ]
    flag_modified(attempt, "flagged_fields")
    events.append(await _flag_event(session, attempt, ts))
    return StatusChange(changed=True, events=events, issued=[])


async def unflag_field(
    session: AsyncSession, attempt: Attempt, ts: TrainingSession, *, field: str
) -> StatusChange:
    """Removes the flag from a field (until the card is closed)."""
    if attempt.state in CLOSED_STATES:
        raise ApiError(
            409, "card_closed", "Работа с карточкой завершена: снять отметку уже нельзя."
        )
    kept = [f for f in attempt.flagged_fields if f.get("field") != field]
    if len(kept) == len(attempt.flagged_fields):
        return StatusChange(changed=False, events=[], issued=[])
    attempt.flagged_fields = kept
    flag_modified(attempt, "flagged_fields")
    return StatusChange(changed=True, events=[await _flag_event(session, attempt, ts)], issued=[])


async def _flag_event(session: AsyncSession, attempt: Attempt, ts: TrainingSession) -> SessionEvent:
    """The teacher's monitoring learns that the trainee is checking the data of the card:
    ``attempt.progress`` as for the other actions, with the flagged fields."""
    return await append_event(
        session,
        session_id=ts.id,
        type_="attempt.progress",
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "stage": "checking_data",
            "flagged_fields": [f["field"] for f in attempt.flagged_fields],
        },
    )


async def finish_attempt(
    session: AsyncSession, attempt: Attempt, ts: TrainingSession, student: User
) -> StatusChange:
    """«Завершить работу с карточкой» from the training panel: closes the card as it is."""
    if attempt.state in CLOSED_STATES:
        return StatusChange(changed=False, events=[], issued=[])
    events: list[SessionEvent] = []
    if attempt.state == ATTEMPT_ISSUED:
        events += await open_attempt(session, attempt, ts)
    attempt.state = ATTEMPT_FINISHED
    attempt.submitted_at = utcnow()
    events.extend(await _end_service_calls(session, attempt))
    events.append(await _submitted_event(session, attempt))
    card = await load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    events.append(await evaluate_and_store(session, attempt, ts, card.body))
    return StatusChange(changed=True, events=events, issued=[])


async def _end_service_calls(session: AsyncSession, attempt: Attempt) -> list[SessionEvent]:
    """Closing the card ends a call to a service officer still in progress (issue #36)."""
    from app.dialog import officer  # the officer dialog imports this module

    return await officer.end_open_calls(session, attempt)


async def _card_status_event(session: AsyncSession, attempt: Attempt) -> SessionEvent:
    return await append_event(
        session,
        session_id=attempt.session_id,
        type_="card.status_changed",
        student_id=attempt.student_id,
        payload={"attempt_id": attempt.id, "card_status": attempt.card_status},
    )


async def _submitted_event(session: AsyncSession, attempt: Attempt) -> SessionEvent:
    return await append_event(
        session,
        session_id=attempt.session_id,
        type_="attempt.submitted",
        student_id=attempt.student_id,
        payload=_attempt_event_payload(attempt),
    )


def evaluation_input(attempt: Attempt) -> dict:
    """The attempt as the evaluation engine expects it: for a card (PRD 9.2) the dispatcher
    entries of the status log without the bookkeeping fields, for a call (PRD 9.3) the
    submitted card, the transcript and the call marks."""
    if attempt.mode == MODE_CALL_INTAKE:
        return call_intake_input(attempt)
    return {
        "issued_at": attempt.issued_at,
        "received_at": attempt.received_at,
        "status_log": [
            {
                "status": e["status"],
                "at": e["at"],
                "order_number": e.get("order_number"),
                "comment": e.get("comment"),
                "reject_reason": e.get("reject_reason"),
            }
            for e in attempt.status_log
            if e.get("by") != BY_SYSTEM
        ],
        "flagged_fields": [
            {"field": f["field"], "corrected_value": f["corrected_value"], "at": f["at"]}
            for f in attempt.flagged_fields or []
        ],
        "service_calls": [
            {
                "service": c["service"],
                "started_at": c["started_at"],
                "answered": bool(c.get("answered")),
                "ended_at": c.get("ended_at"),
                "dialog": [
                    {
                        "role": t["role"],
                        "text": t["text"],
                        "topics": list(t.get("topics") or []),
                        "at": t.get("at"),
                    }
                    for t in c.get("dialog") or []
                ],
                "facts_passed": list(c.get("facts_passed") or []),
            }
            for c in attempt.service_calls or []
        ],
    }


def call_intake_input(attempt: Attempt) -> dict:
    """PRD 9.3 input: the card as submitted (or the draft, when the lesson closed the call),
    the dialog and the marks of the call panel («нет контакта», «срыв звонка»)."""
    from app.dialog.service import dialog_input
    from app.intake.service import evaluation_card

    return {
        # The conversation timer starts when the operator picks up; a card saved without a
        # single answered call counts from the moment the call rang.
        "answered_at": attempt.answered_at or attempt.received_at or attempt.issued_at,
        "submitted_at": attempt.submitted_at,
        "card": evaluation_card(attempt.draft),
        "dialog": dialog_input(attempt),
        "call_dropped_marked": bool(attempt.call_dropped_marked),
        "no_contact_marked": bool(attempt.no_contact_marked),
    }


async def evaluate_and_store(
    session: AsyncSession, attempt: Attempt, ts: TrainingSession, body: dict
) -> SessionEvent:
    """Scores a closed card synchronously (PRD 9.2: within 2 s) and stores the result."""
    # The lesson norm (session setting) is what the trainee was told; the scenario's own
    # value is only a default for the editor.
    result = await evaluate_attempt(
        {**body, "norm_seconds": ts.norm_seconds},
        evaluation_input(attempt),
        weights=ts.weights or None,
        pass_threshold=ts.pass_threshold,
    )
    data = result.to_dict()
    row = await session.get(Evaluation, attempt.id)
    first_evaluation = row is None
    if row is None:
        row = Evaluation(attempt_id=attempt.id, total=0, passed=False)
        session.add(row)
    row.total = data["total"]
    row.passed = data["passed"]
    row.components = data["components"]
    row.errors = data["errors"]
    row.methods = data["methods"]
    attempt.result = data
    attempt.state = ATTEMPT_EVALUATED
    await session.flush()
    # The skill rating moves once per attempt (PRD 9.7), not on a re-evaluation.
    if first_evaluation:
        await ratings.apply_evaluation(session, attempt, data["total"])
    return await append_event(
        session,
        session_id=ts.id,
        type_="attempt.evaluated",
        student_id=attempt.student_id,
        payload={
            "attempt_id": attempt.id,
            "total": data["total"],
            "passed": data["passed"],
            "state": attempt.state,
        },
    )


def transitions_for(attempt: Attempt, service: Service | None) -> list[Transition]:
    if attempt.state in CLOSED_STATES:
        return []
    codes = machine.allowed_next(
        attempt.response_status, no_reject=bool(service and service.no_reject)
    )
    result = []
    for code in codes:
        spec = machine.STATUSES[code]
        if spec["is_system"]:
            continue
        result.append(
            Transition(
                code=code,
                title=spec["title"],
                requires_comment=spec["requires_comment"],
                requires_order_number=spec["requires_order_number"],
                is_final=spec["is_final"],
                is_primary=spec["is_primary"],
            )
        )
    result.sort(key=lambda t: machine.STATUSES[t.code]["order"])
    return result


# ---------------------------------------------------------------- background sweep


async def sweep_not_notified(
    session: AsyncSession, now: datetime | None = None
) -> list[SessionEvent]:
    """Marks cards that got no primary status within the session norm as «Не оповещено»
    and accepted cards still open after the session's threshold as «Не завершено»
    (memo, page 27). Safe to run from several replicas: each card changes once."""
    now = now or utcnow()
    rows = await session.execute(
        select(Attempt, TrainingSession.norm_seconds, TrainingSession.unfinished_seconds)
        .join(TrainingSession, TrainingSession.id == Attempt.session_id)
        .where(
            Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
            Attempt.card_status == CARD_REGISTERED,
            # A call has no «Не оповещено»: the conversation timer is scored instead.
            Attempt.mode != MODE_CALL_INTAKE,
        )
        .with_for_update(of=Attempt, skip_locked=True)
    )
    events: list[SessionEvent] = []
    for attempt, norm_seconds, unfinished_seconds in rows:
        if attempt.primary_status_at is None:
            if attempt.issued_at + timedelta(seconds=norm_seconds) > now:
                continue
            attempt.card_status = CARD_NOT_NOTIFIED
        else:
            if attempt.primary_status_at + timedelta(seconds=unfinished_seconds) > now:
                continue
            attempt.card_status = CARD_NOT_FINISHED
        events.append(await _card_status_event(session, attempt))
    return events
