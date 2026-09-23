"""Teacher side of a training session: groups, creating a session, the card queue, starting
and finishing (PRD 11, 13.7). Like ``service``, every function works inside the caller's
transaction and returns the events to publish after commit.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.domain.evaluation import apply_weights
from app.domain.evaluation import call_intake as call_intake_engine
from app.domain.evaluation import card_response as card_response_engine
from app.errors import ApiError
from app.events import append_event
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    ATTEMPT_ISSUED,
    MODE_CALL_INTAKE,
    MODE_CARD_RESPONSE,
    SCENARIO_APPROVED,
    SESSION_DRAFT,
    SESSION_FINISHED,
    SESSION_RUNNING,
    Attempt,
    Group,
    GroupMember,
    IncidentGroup,
    Role,
    Scenario,
    Service,
    SessionEvent,
    TrainingSession,
    User,
)
from app.training import service as training

# Where the cards of a session come from (PRD 9.6 item 6, «источник карточек» of the ТЗ):
# «scenarios» / «mixed» — every approved scenario; «generated» — tickets, generation and hand-made
# ones (everything but trainees' cards); «student_made» — cards trainees saved in call intake and
# the teacher approved as scenarios (``scenarios.source = student``).
CARD_SOURCE_SCENARIOS = "scenarios"
CARD_SOURCE_GENERATED = "generated"
CARD_SOURCE_STUDENT_MADE = "student_made"
CARD_SOURCE_MIXED = "mixed"
CARD_SOURCES = (
    CARD_SOURCE_SCENARIOS,
    CARD_SOURCE_GENERATED,
    CARD_SOURCE_STUDENT_MADE,
    CARD_SOURCE_MIXED,
)
SCENARIO_SOURCE_STUDENT = "student"
# How the caller answers in call-intake sessions (PRD 9.3); the provider is in app.providers.dialog.
DIALOG_MODES = ("select", "hybrid", "generate", "buttons", "live", "cloud")
# The caller lives in Vapi (plan/track-c-vapi.md); only where CLOUD_VOICE_ENABLED=true.
DIALOG_MODE_CLOUD = "cloud"

DIFFICULTY_RANGE = (1, 3)
NORM_RANGE = (5, 600)
UNFINISHED_RANGE = (60, 7 * 24 * 3600)
CARDS_PER_STUDENT_MAX = 50


# ---------------------------------------------------------------- groups


async def teacher_groups(session: AsyncSession, teacher: User) -> list[Group]:
    return list(
        await session.scalars(
            select(Group).where(Group.teacher_id == teacher.id).order_by(Group.created_at)
        )
    )


async def group_members(
    session: AsyncSession, group_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[User]]:
    """Members per group, ordered by name."""
    if not group_ids:
        return {}
    rows = await session.execute(
        select(GroupMember.group_id, User)
        .join(User, User.id == GroupMember.student_id)
        .where(GroupMember.group_id.in_(group_ids))
        .order_by(User.full_name)
    )
    result: dict[uuid.UUID, list[User]] = {gid: [] for gid in group_ids}
    for group_id, user in rows:
        result[group_id].append(user)
    return result


async def own_group(session: AsyncSession, group_id: uuid.UUID, teacher: User) -> Group:
    group = await session.get(Group, group_id)
    if group is None:
        raise ApiError(404, "group_not_found", "Группа не найдена.")
    if group.teacher_id != teacher.id:
        raise ApiError(403, "foreign_group", "Это группа другого преподавателя.")
    return group


async def check_students(session: AsyncSession, student_ids: list[uuid.UUID]) -> list[User]:
    ids = list(dict.fromkeys(student_ids))
    if not ids:
        return []
    users = {u.id: u for u in await session.scalars(select(User).where(User.id.in_(ids)))}
    missing = [i for i in ids if i not in users or users[i].role != Role.student]
    if missing:
        raise ApiError(
            422, "unknown_student", "Среди выбранных есть кто-то, кто не является обучающимся."
        )
    return [users[i] for i in ids]


async def set_members(session: AsyncSession, group: Group, students: list[User]) -> None:
    current = {
        m.student_id: m
        for m in await session.scalars(select(GroupMember).where(GroupMember.group_id == group.id))
    }
    wanted = {s.id for s in students}
    for student_id, member in current.items():
        if student_id not in wanted:
            await session.delete(member)
    for student_id in wanted - set(current):
        session.add(GroupMember(group_id=group.id, student_id=student_id))
    await session.flush()


# ---------------------------------------------------------------- sessions


async def own_session(
    session: AsyncSession, session_id: uuid.UUID, teacher: User
) -> TrainingSession:
    """The session if ``teacher`` owns it; a colleague's session is 403 (plan wave 4)."""
    ts = await session.get(TrainingSession, session_id)
    if ts is None:
        raise ApiError(404, "session_not_found", "Занятие не найдено.")
    if ts.teacher_id != teacher.id:
        raise ApiError(403, "foreign_session", "Это занятие другого преподавателя.")
    return ts


@dataclass
class SessionSettings:
    title: str
    mode: str
    group_id: uuid.UUID
    card_source: str
    scenario_ids: list[uuid.UUID]
    incident_groups: list[str]
    difficulty: int
    service_profile: list[str]
    norm_seconds: int
    pass_threshold: int
    hints_enabled: bool
    cards_per_student: int
    unfinished_seconds: int
    weights: dict[str, int]
    voice_enabled: bool = False
    dialog_mode: str = "select"
    adaptive: bool = False


async def validate_settings(session: AsyncSession, spec: SessionSettings, teacher: User) -> None:
    """Rejects settings the trainer cannot run; messages say what to change."""
    if spec.mode not in (MODE_CARD_RESPONSE, MODE_CALL_INTAKE):
        raise ApiError(422, "bad_mode", "Неизвестный режим занятия.")
    if spec.dialog_mode not in DIALOG_MODES:
        raise ApiError(422, "bad_dialog_mode", f"Режим диалога: один из {', '.join(DIALOG_MODES)}.")
    if spec.dialog_mode == DIALOG_MODE_CLOUD:
        if spec.mode != MODE_CALL_INTAKE:
            # Облако играет только заявителя на входящем вызове: служебные звонки ДДС идут
            # через локальный конвейер (plan/track-c-vapi.md).
            raise ApiError(
                422,
                "cloud_voice_not_for_cards",
                "Облачный голос есть только в приёме вызова: в реагировании на карточку "
                "службу и бригаду играет локальная модель.",
            )
        if not get_settings().cloud_voice_enabled:
            raise ApiError(
                422,
                "cloud_voice_disabled",
                "Облачный голос выключен на этой установке (CLOUD_VOICE_ENABLED): "
                "выберите другой режим диалога.",
            )
    if spec.card_source not in CARD_SOURCES:
        raise ApiError(
            422,
            "bad_card_source",
            f"Источник карточек: один из {', '.join(CARD_SOURCES)}.",
        )
    await own_group(session, spec.group_id, teacher)
    lo, hi = DIFFICULTY_RANGE
    if not lo <= spec.difficulty <= hi:
        raise ApiError(422, "bad_difficulty", f"Сложность: от {lo} до {hi}.")
    lo, hi = NORM_RANGE
    if not lo <= spec.norm_seconds <= hi:
        raise ApiError(422, "bad_norm", f"Норматив: от {lo} до {hi} секунд.")
    if not 0 <= spec.pass_threshold <= 100:
        raise ApiError(422, "bad_threshold", "Порог зачёта: от 0 до 100 баллов.")
    lo, hi = UNFINISHED_RANGE
    if not lo <= spec.unfinished_seconds <= hi:
        raise ApiError(
            422, "bad_unfinished", f"Порог «Не завершено»: от {lo} секунд до {hi // 3600} часов."
        )
    if not 0 <= spec.cards_per_student <= CARDS_PER_STUDENT_MAX:
        raise ApiError(
            422,
            "bad_cards",
            f"Карточек на обучающегося: от 0 (вся очередь) до {CARDS_PER_STUDENT_MAX}.",
        )
    if spec.incident_groups:
        known = set(
            await session.scalars(
                select(IncidentGroup.code).where(IncidentGroup.code.in_(spec.incident_groups))
            )
        )
        unknown = [g for g in spec.incident_groups if g not in known]
        if unknown:
            raise ApiError(
                422, "unknown_incident_group", f"Нет таких групп происшествий: {unknown}."
            )
    if spec.service_profile:
        known = set(
            await session.scalars(
                select(Service.code).where(Service.code.in_(spec.service_profile))
            )
        )
        unknown = [s for s in spec.service_profile if s not in known]
        if unknown:
            raise ApiError(422, "unknown_service", f"Нет таких служб: {unknown}.")
    engine = call_intake_engine if spec.mode == MODE_CALL_INTAKE else card_response_engine
    try:
        apply_weights(engine.DEFAULT_WEIGHTS, spec.weights or None)
    except ValueError as exc:
        raise ApiError(422, "bad_weights", f"Веса оценки: {exc}.") from exc
    if spec.scenario_ids:
        found = set(
            await session.scalars(
                select(Scenario.id).where(
                    Scenario.id.in_(spec.scenario_ids),
                    Scenario.kind == spec.mode,
                    Scenario.status == SCENARIO_APPROVED,
                )
            )
        )
        if len(found) != len(set(spec.scenario_ids)):
            raise ApiError(
                422, "unknown_scenario", "Среди выбранных сценариев есть неутверждённые."
            )


def apply_settings(ts: TrainingSession, spec: SessionSettings) -> None:
    ts.title = spec.title
    ts.mode = spec.mode
    ts.group_id = spec.group_id
    ts.card_source = spec.card_source
    ts.scenario_ids = list(spec.scenario_ids)
    ts.incident_groups = list(spec.incident_groups)
    ts.difficulty = spec.difficulty
    ts.service_profile = list(spec.service_profile)
    ts.norm_seconds = spec.norm_seconds
    ts.pass_threshold = spec.pass_threshold
    ts.hints_enabled = spec.hints_enabled
    ts.cards_per_student = spec.cards_per_student
    ts.unfinished_seconds = spec.unfinished_seconds
    ts.weights = dict(spec.weights)
    ts.voice_enabled = spec.voice_enabled
    ts.dialog_mode = spec.dialog_mode
    ts.adaptive = spec.adaptive


async def pick_scenarios(session: AsyncSession, ts: TrainingSession) -> list[Scenario]:
    """Queue of a session without an explicit one: approved scenarios of the mode filtered by
    service profile and incident groups, not harder than the session difficulty (when none
    match, harder ones are taken rather than nothing), shuffled inside each difficulty so
    easier cards come first (the duplicate card follows its original). Random for now; the
    adaptive selection by ratings reorders it per trainee in ``service.issue_cards``."""
    if ts.scenario_ids:
        return await training.scenario_queue(session, ts)
    query = select(Scenario).where(Scenario.kind == ts.mode, Scenario.status == SCENARIO_APPROVED)
    if ts.card_source == CARD_SOURCE_GENERATED:
        query = query.where(Scenario.source != SCENARIO_SOURCE_STUDENT)
    elif ts.card_source == CARD_SOURCE_STUDENT_MADE:
        query = query.where(Scenario.source == SCENARIO_SOURCE_STUDENT)
    if ts.service_profile and ts.mode != MODE_CALL_INTAKE:
        query = query.where(Scenario.service_code.in_(ts.service_profile))
    rows = list(await session.scalars(query))
    if ts.incident_groups:
        rows = [
            s
            for s in rows
            if s.incident_type_code and s.incident_type_code.split(".")[0] in ts.incident_groups
        ]
    easy = [s for s in rows if s.difficulty <= ts.difficulty]
    rows = easy or rows
    random.shuffle(rows)
    rows.sort(key=lambda s: s.difficulty)
    return rows


async def start_session(
    session: AsyncSession, ts: TrainingSession, teacher: User
) -> list[SessionEvent]:
    """Draft → running. Fixes the card queue so every trainee gets the same set, and tells
    the group (``session.started`` is addressed to everyone in the session)."""
    if ts.status == SESSION_RUNNING:
        return []
    if ts.status == SESSION_FINISHED:
        raise ApiError(409, "session_finished", "Занятие уже завершено: создайте новое.")
    members = await group_members(session, [ts.group_id] if ts.group_id else [])
    if not members.get(ts.group_id):
        raise ApiError(
            422, "empty_group", "В группе нет обучающихся: добавьте их в разделе «Группы»."
        )
    queue = await pick_scenarios(session, ts)
    if not queue:
        raise ApiError(
            422,
            "empty_queue",
            "Под выбранные группы происшествий, службы и сложность нет утверждённых сценариев.",
        )
    ts.scenario_ids = [s.id for s in queue]
    ts.status = SESSION_RUNNING
    ts.started_at = training.utcnow()
    await session.flush()
    return [
        await append_event(
            session,
            session_id=ts.id,
            type_="session.started",
            payload={"session_id": ts.id, "started_at": ts.started_at, "cards": len(queue)},
        )
    ]


async def finish_session(
    session: AsyncSession, ts: TrainingSession, teacher: User
) -> list[SessionEvent]:
    """Running → finished. Cards nobody opened are withdrawn (the trainee never got to them);
    cards in work are closed as they are and scored, so the report is complete."""
    if ts.status == SESSION_FINISHED:
        return []
    if ts.status == SESSION_DRAFT:
        raise ApiError(409, "session_not_started", "Занятие ещё не начато.")
    events: list[SessionEvent] = []
    # Finished first, so closing a card issues no new ones.
    ts.status = SESSION_FINISHED
    ts.finished_at = training.utcnow()
    active = list(
        await session.scalars(
            select(Attempt).where(
                Attempt.session_id == ts.id, Attempt.state.in_(ACTIVE_ATTEMPT_STATES)
            )
        )
    )
    withdrawn = 0
    for attempt in active:
        if attempt.state == ATTEMPT_ISSUED:
            await session.delete(attempt)
            withdrawn += 1
            continue
        student = await session.get(User, attempt.student_id)
        change = await training.finish_attempt(session, attempt, ts, student)
        events += change.events
    await session.flush()
    events.append(
        await append_event(
            session,
            session_id=ts.id,
            type_="session.finished",
            payload={
                "session_id": ts.id,
                "finished_at": ts.finished_at,
                "closed": len(active) - withdrawn,
                "withdrawn": withdrawn,
            },
        )
    )
    return events


async def attempt_counts(
    session: AsyncSession, session_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, float]]:
    """Per session: evaluated attempts, their mean total and how many trainees took part."""
    if not session_ids:
        return {}
    rows = await session.execute(
        select(
            Attempt.session_id,
            func.count(Attempt.result),
            func.avg((Attempt.result["total"]).as_float()),
            func.count(func.distinct(Attempt.student_id)),
        )
        .where(Attempt.session_id.in_(session_ids))
        .group_by(Attempt.session_id)
    )
    return {
        sid: {"evaluated": int(n), "average": avg, "students": int(students)}
        for sid, n, avg, students in rows
    }
