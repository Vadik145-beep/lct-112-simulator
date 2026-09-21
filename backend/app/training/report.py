"""Session report and monitoring snapshot for the teacher (PRD 13.7).

The report aggregates stored evaluations (``attempts.result``): mean score, time to the
primary status against the norm, wrong decisions, typical errors, grammar; each trainee
expands to attempts. The monitoring snapshot is the same data for a running session with the
cards in work, updated on the client from session events.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evaluation.data_check import field_title
from app.domain.evaluation.timing import seconds_between
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    Attempt,
    Scenario,
    TrainingSession,
    User,
)
from app.models.cabinets import Comment, EvaluationOverride
from app.training import present, review
from app.training import service as training
from app.training import sessions as lessons
from app.training.teacher_schemas import (
    MonitorCard,
    MonitorOut,
    MonitorStudent,
    ReportAction,
    ReportAttempt,
    ReportErrorCount,
    ReportOut,
    ReportStudent,
    ReportSummary,
)


@dataclass
class _Acc:
    attempts: list[ReportAttempt] = field(default_factory=list)
    totals: list[float] = field(default_factory=list)
    seconds: list[float] = field(default_factory=list)
    grammar: list[float] = field(default_factory=list)
    passed: int = 0
    wrong_decisions: int = 0
    errors: Counter = field(default_factory=Counter)
    error_titles: dict[str, str] = field(default_factory=dict)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _primary_seconds(attempt: Attempt) -> float | None:
    if attempt.primary_status_at is None:
        return None
    return round(seconds_between(attempt.issued_at, attempt.primary_status_at), 1)


def _decision(result: dict | None) -> tuple[str | None, str | None]:
    if not result:
        return None, None
    items = (result.get("components") or {}).get("decision", {}).get("items") or []
    first = items[0] if items else {}
    return first.get("expected"), first.get("actual")


def _grammar_percent(result: dict | None) -> float | None:
    if not result:
        return None
    grammar = (result.get("components") or {}).get("grammar") or {}
    if grammar.get("status") != "checked" or not grammar.get("max"):
        return None
    return round(100 * float(grammar["score"]) / float(grammar["max"]), 1)


def _remarks(
    errors: list[dict], override: EvaluationOverride | None, comments: list[Comment]
) -> list[str]:
    remarks = []
    for e in errors:
        title = str(e.get("title") or e.get("code"))
        explanation = str(e.get("explanation") or "").strip()
        remarks.append(f"{title}: {explanation}" if explanation else title)
    if override is not None:
        remarks.append(
            f"Оценка изменена преподавателем: {override.old_total} → {override.new_total} "
            f"({override.reason})"
        )
    remarks += [f"Комментарий: {c.text}" for c in comments]
    return remarks


def _iso(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _action(start: datetime, at: datetime | None, kind: str, title: str, detail: str | None):
    if at is None:
        return None
    return ReportAction(
        at=at,
        seconds=round(seconds_between(start, at) or 0.0, 1),
        kind=kind,  # type: ignore[arg-type]
        title=title,
        detail=(detail or "").strip() or None,
    )


def attempt_actions(attempt: Attempt) -> list[ReportAction]:
    """The trainee's steps in time order: statuses with comments, flagged fields, calls to
    services, questions asked in the call and the saved card. System entries («Добавлена»,
    «Получена службой») are skipped: they are not the trainee's actions."""
    start = attempt.issued_at
    actions: list[ReportAction | None] = []
    for e in attempt.status_log:
        if e.get("by") == training.BY_SYSTEM:
            continue
        parts = []
        if e.get("order_number"):
            parts.append(f"наряд {e['order_number']}")
        if e.get("comment"):
            parts.append(str(e["comment"]))
        actions.append(
            _action(
                start,
                _iso(e.get("at")),
                "status",
                training.status_title(e["status"]),
                "; ".join(parts),
            )
        )
    for f in attempt.flagged_fields:
        value = f.get("corrected_value")
        actions.append(
            _action(
                start,
                _iso(f.get("at")),
                "flag",
                f"Отмечена ошибка: {field_title(f['field'])}",
                f"верно: {value}" if value else None,
            )
        )
    for c in attempt.service_calls:
        who = c.get("service_title") or c.get("service")
        if c.get("kind") == "report":
            # The squad's report (customer, 21.09.2026): what it announced and whether the
            # trainee picked it up.
            spoken = any(t.get("role") == "operator" for t in c.get("dialog") or [])
            taken = "принят" if spoken else "не принят"
            if c.get("end_reason") == "not_taken":
                taken = "не принят, закрыт по времени"
            actions.append(
                _action(
                    start,
                    _iso(c.get("started_at")),
                    "call",
                    f"Доклад бригады: {who}",
                    f"«{training.status_title(c.get('report_status') or '')}», {taken}",
                )
            )
            continue
        facts = ", ".join(str(x) for x in c.get("facts_passed") or [])
        actions.append(
            _action(
                start,
                _iso(c.get("started_at")),
                "call",
                f"Звонок в службу: {who}",
                f"передано: {facts}" if facts else "факты не переданы",
            )
        )
    if attempt.mode == training.MODE_CALL_INTAKE:
        for t in attempt.dialog:
            if t.get("role") != "operator":
                continue
            actions.append(
                _action(start, _iso(t.get("at")), "question", "Вопрос заявителю", t.get("text"))
            )
        actions.append(_action(start, attempt.submitted_at, "card", "Карточка сохранена", None))
    present_actions = [a for a in actions if a is not None]
    present_actions.sort(key=lambda a: a.at)
    return present_actions


def report_attempt(
    attempt: Attempt,
    ts: TrainingSession,
    scenario: Scenario | None,
    incident_title: str,
    card_status_title: str,
    override: EvaluationOverride | None = None,
    comments: list[Comment] | None = None,
) -> ReportAttempt:
    result = attempt.result
    expected, actual = _decision(result)
    seconds = _primary_seconds(attempt)
    errors = [e for e in (result or {}).get("errors") or []]
    comments = comments or []
    return ReportAttempt(
        id=attempt.id,
        card_number=attempt.card_number,
        scenario_title=scenario.title if scenario else "",
        incident_title=incident_title,
        state=attempt.state,
        card_status=attempt.card_status,
        card_status_title=card_status_title,
        issued_at=attempt.issued_at,
        submitted_at=attempt.submitted_at,
        total=result.get("total") if result else None,
        passed=result.get("passed") if result else None,
        seconds=seconds,
        norm_seconds=ts.norm_seconds,
        deviation=None if seconds is None else round(seconds - ts.norm_seconds, 1),
        decision_expected=expected,
        decision_actual=actual,
        decision_correct=None if expected is None else expected == actual,
        errors=[str(e.get("title") or e.get("code")) for e in errors],
        grammar_percent=_grammar_percent(result),
        remarks=_remarks(errors, override, comments),
        overridden=override is not None,
        override_reason=override.reason if override else None,
        comments=[c.text for c in comments],
        actions=attempt_actions(attempt),
    )


@dataclass
class _Loaded:
    members: list[User]
    attempts: list[Attempt]
    scenarios: dict[uuid.UUID, Scenario]
    incident_titles: dict[uuid.UUID, str]
    card_status_titles: dict[str, str]
    overrides: dict[uuid.UUID, EvaluationOverride] = field(default_factory=dict)
    comments: dict[uuid.UUID, list[Comment]] = field(default_factory=dict)


async def _load(session: AsyncSession, ts: TrainingSession) -> _Loaded:
    members = (await lessons.group_members(session, [ts.group_id] if ts.group_id else [])).get(
        ts.group_id, []
    )
    attempts = list(
        await session.scalars(
            select(Attempt).where(Attempt.session_id == ts.id).order_by(Attempt.issued_at)
        )
    )
    scenario_ids = {a.scenario_id for a in attempts}
    scenarios = {
        s.id: s
        for s in await session.scalars(select(Scenario).where(Scenario.id.in_(scenario_ids)))
    }
    versions = await present.load_versions(session, attempts)
    lookups = await present.load_lookups(
        session,
        {
            (v.body.get("card") or {}).get("incident_type")
            for v in versions.values()
            if v.body.get("card")
        },
    )
    titles: dict[uuid.UUID, str] = {}
    for a in attempts:
        version = versions.get((a.scenario_id, a.scenario_version))
        body = version.body if version else {}
        code = (body.get("card") or {}).get("incident_type")
        itype = lookups.incident_types.get(code or "")
        titles[a.id] = (
            itype.final_title
            if itype
            else str((body.get("card") or {}).get("incident_title") or "")
        )
    # Trainees who took part but left the group still belong in the report.
    known = {u.id for u in members}
    extra_ids = {a.student_id for a in attempts} - known
    if extra_ids:
        members += list(await session.scalars(select(User).where(User.id.in_(extra_ids))))
    attempt_ids = [a.id for a in attempts]
    return _Loaded(
        members=members,
        attempts=attempts,
        scenarios=scenarios,
        incident_titles=titles,
        card_status_titles={c.code: c.title for c in lookups.card_statuses.values()},
        overrides=await review.latest_overrides(session, attempt_ids),
        comments=await review.comments_for(session, attempt_ids),
    )


async def build_report(session: AsyncSession, ts: TrainingSession) -> ReportOut:
    loaded = await _load(session, ts)
    members, attempts, scenarios = loaded.members, loaded.attempts, loaded.scenarios
    per_student: dict[uuid.UUID, _Acc] = {u.id: _Acc() for u in members}
    for a in attempts:
        acc = per_student.setdefault(a.student_id, _Acc())
        row = report_attempt(
            a,
            ts,
            scenarios.get(a.scenario_id),
            loaded.incident_titles[a.id],
            loaded.card_status_titles.get(a.card_status, a.card_status),
            loaded.overrides.get(a.id),
            loaded.comments.get(a.id),
        )
        acc.attempts.append(row)
        if row.total is None:
            continue
        acc.totals.append(float(row.total))
        acc.passed += 1 if row.passed else 0
        if row.seconds is not None:
            acc.seconds.append(row.seconds)
        if row.decision_correct is False:
            acc.wrong_decisions += 1
        if row.grammar_percent is not None:
            acc.grammar.append(row.grammar_percent)
        for e in (a.result or {}).get("errors") or []:
            code = str(e.get("code") or e.get("title"))
            acc.errors[code] += 1
            acc.error_titles[code] = str(e.get("title") or code)

    students: list[ReportStudent] = []
    session_errors: Counter = Counter()
    error_titles: dict[str, str] = {}
    all_totals: list[float] = []
    all_seconds: list[float] = []
    all_passed = 0
    for user in sorted(members, key=lambda u: u.full_name):
        acc = per_student[user.id]
        mean_seconds = _mean(acc.seconds)
        students.append(
            ReportStudent(
                student_id=user.id,
                full_name=user.full_name,
                login=user.login,
                service_code=user.service_code,
                attempts=acc.attempts,
                attempts_total=len(acc.attempts),
                evaluated=len(acc.totals),
                passed=acc.passed,
                average=_mean(acc.totals),
                average_seconds=mean_seconds,
                average_deviation=None
                if mean_seconds is None
                else round(mean_seconds - ts.norm_seconds, 1),
                wrong_decisions=acc.wrong_decisions,
                typical_errors=[
                    ReportErrorCount(code=c, title=acc.error_titles[c], count=n)
                    for c, n in acc.errors.most_common()
                ],
                grammar_percent=_mean(acc.grammar),
            )
        )
        session_errors.update(acc.errors)
        error_titles.update(acc.error_titles)
        all_totals += acc.totals
        all_seconds += acc.seconds
        all_passed += acc.passed
    return ReportOut(
        session_id=ts.id,
        title=ts.title,
        status=ts.status,
        started_at=ts.started_at,
        finished_at=ts.finished_at,
        norm_seconds=ts.norm_seconds,
        pass_threshold=ts.pass_threshold,
        summary=ReportSummary(
            students=len(students),
            participated=sum(1 for s in students if s.attempts_total),
            evaluated=len(all_totals),
            passed=all_passed,
            average=_mean(all_totals),
            average_seconds=_mean(all_seconds),
            typical_errors=[
                ReportErrorCount(code=c, title=error_titles[c], count=n)
                for c, n in session_errors.most_common(5)
            ],
        ),
        students=students,
    )


def monitor_card(attempt: Attempt, ts: TrainingSession, incident_title: str) -> MonitorCard:
    return MonitorCard(
        attempt_id=attempt.id,
        card_number=attempt.card_number,
        incident_title=incident_title,
        state=attempt.state,
        response_status=attempt.response_status,
        response_status_title=training.status_title(attempt.response_status),
        card_status=attempt.card_status,
        issued_at=attempt.issued_at,
        received_at=attempt.received_at,
        primary_status_at=attempt.primary_status_at,
        submitted_at=attempt.submitted_at,
        total=attempt.result.get("total") if attempt.result else None,
        passed=attempt.result.get("passed") if attempt.result else None,
    )


async def build_monitor(session: AsyncSession, ts: TrainingSession) -> MonitorOut:
    # The sequence is read before the attempts: an event committed in between is then
    # replayed by the WebSocket (applying it to a snapshot that already reflects it is
    # harmless), whereas the other order would lose it.
    last_seq = await present.last_seq(session, ts.id)
    loaded = await _load(session, ts)
    members, attempts, titles = loaded.members, loaded.attempts, loaded.incident_titles
    by_student: dict[uuid.UUID, list[Attempt]] = {u.id: [] for u in members}
    for a in attempts:
        by_student.setdefault(a.student_id, []).append(a)
    tiles: list[MonitorStudent] = []
    for user in sorted(members, key=lambda u: u.full_name):
        rows = by_student[user.id]
        totals = [float(a.result["total"]) for a in rows if a.result]
        last: datetime | None = None
        last_total: float | None = None
        for a in rows:
            if a.result and a.submitted_at and (last is None or a.submitted_at > last):
                last, last_total = a.submitted_at, float(a.result["total"])
        tiles.append(
            MonitorStudent(
                student_id=user.id,
                full_name=user.full_name,
                login=user.login,
                service_code=user.service_code,
                active=[
                    monitor_card(a, ts, titles[a.id])
                    for a in rows
                    if a.state in ACTIVE_ATTEMPT_STATES
                ],
                finished=len(totals),
                passed=sum(1 for a in rows if a.result and a.result.get("passed")),
                average=_mean(totals),
                last_total=last_total,
                last_attempt_id=next(
                    (a.id for a in rows if a.result and a.submitted_at == last), None
                ),
            )
        )
    return MonitorOut(
        session_id=ts.id,
        status=ts.status,
        norm_seconds=ts.norm_seconds,
        pass_threshold=ts.pass_threshold,
        cards_total=len(ts.scenario_ids),
        students=tiles,
        last_seq=last_seq,
    )
