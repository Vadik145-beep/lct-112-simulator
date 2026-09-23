"""Reports of the squad to the dispatcher on the squad's timeline (customer, 21.09.2026:
«старший группы звонит в ДДС, докладывает об обстановке и ходе работ»).

After «Принята» the squad of the trainee's service departs, arrives, works and finishes at the
moments the scenario sets (``reference.reports``, seconds after the previous milestone). At
each moment the squad leader calls the trainee: a record of kind «report» in
``attempts.service_calls`` (``app.dialog.officer.start_report``). The trainee is expected to
pick up, listen and reflect the report with the matching status; the evaluation compares the
status log with the report times (``detectors.status_before_report``,
``detectors.report_not_reflected``).

``sweep_reports`` runs in the background sweep of the API (``app.training.sweeper``) next to the
«Не оповещено» check, so it works without telephony too: without it the report is answered at
once and shown in the card; with telephony the trainee's phone rings from the squad's number
and the leader speaks after the pick-up (``CallManager.dial_service``). A report nobody talked
to for ``REPORT_CALL_TIMEOUT_SECONDS`` is closed as «not taken» and the timeline goes on.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evaluation import status_machine as machine
from app.domain.evaluation.schemas import BrigadeReport, CardResponseScenario
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    MODE_CARD_RESPONSE,
    Attempt,
    SessionEvent,
    TrainingSession,
)
from app.training import service as training

# A report call left open (nobody spoke) ends after this and the next report may come.
REPORT_CALL_TIMEOUT_SECONDS = 90
# Statuses of the trainee's service at which the squad is out and reports.
RESPONDING_STATUSES = frozenset(
    {machine.ACCEPTED, machine.RESPONSE_STARTED, machine.ARRIVED, machine.WORKS_STARTED}
)


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def accepted_at(attempt: Attempt) -> datetime | None:
    """When the dispatcher first set «Принята» (the squad's timeline starts here)."""
    for entry in attempt.status_log:
        if entry.get("status") == machine.ACCEPTED and entry.get("by") != training.BY_SYSTEM:
            return _at(entry.get("at"))
    return None


def next_report(
    scenario: CardResponseScenario, attempt: Attempt, now: datetime
) -> BrigadeReport | None:
    """The report due now, if any: the first one not delivered yet, once its delay after the
    previous milestone has passed and no call is in progress on the card."""
    from app.dialog import officer

    reports = scenario.reference.reports
    if not reports or attempt.response_status not in RESPONDING_STATUSES:
        return None
    delivered = officer.delivered_reports(attempt)
    pending = [r for r in reports if r.status not in delivered]
    if not pending or officer.open_call(attempt) is not None:
        return None
    report = pending[0]
    previous = officer.reports_of(attempt)
    if previous:
        last = previous[-1]
        base = _at(last.get("ended_at")) or (
            (_at(last["started_at"]) or now) + timedelta(seconds=REPORT_CALL_TIMEOUT_SECONDS)
        )
    else:
        base = accepted_at(attempt)
        if base is None:
            return None
        # The squad leaves after the dispatcher's call to the officer, when there was one.
        for call in officer.calls_of(attempt):
            ended = _at(call.get("ended_at"))
            if not officer.is_report(call) and call.get("answered") and ended and ended > base:
                base = ended
    if base + timedelta(seconds=report.after_seconds) > now:
        return None
    return report


def stale_report(attempt: Attempt, now: datetime) -> dict | None:
    """An open report the dispatcher has not spoken to for the timeout."""
    from app.dialog import officer

    call = officer.open_call(attempt)
    if call is None or not officer.is_report(call):
        return None
    if any(t.get("role") == "operator" for t in call.get("dialog") or []):
        return None
    started = _at(call["started_at"]) or now
    if started + timedelta(seconds=REPORT_CALL_TIMEOUT_SECONDS) > now:
        return None
    return call


async def sweep_reports(
    session: AsyncSession, now: datetime | None = None
) -> tuple[list[SessionEvent], list[tuple[Attempt, dict]]]:
    """Delivers the reports due on every active card and closes the stale ones. Returns the
    events to publish and the report calls that should ring the trainee's phone (the caller
    dials them once the transaction is committed)."""
    from app.dialog import officer
    from app.telephony import service as telephony

    now = now or training.utcnow()
    rows = await session.execute(
        select(Attempt, TrainingSession)
        .join(TrainingSession, TrainingSession.id == Attempt.session_id)
        .where(
            Attempt.mode == MODE_CARD_RESPONSE,
            Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
            Attempt.response_status.in_(sorted(RESPONDING_STATUSES)),
        )
        .with_for_update(of=Attempt, skip_locked=True)
    )
    events: list[SessionEvent] = []
    to_dial: list[tuple[Attempt, dict]] = []
    live = telephony.telephony_active()
    for attempt, ts in rows:
        stale = stale_report(attempt, now)
        if stale is not None:
            _, ended = await officer.end(session, attempt, stale["id"], officer.END_NOT_TAKEN)
            events.extend(ended)
            if live:
                manager = telephony.get_service()
                if manager is not None:
                    await manager.calls.hangup_service_call(stale["id"])
            continue
        card = await training.load_scenario_card(
            session, attempt.scenario_id, attempt.scenario_version
        )
        scenario = CardResponseScenario.model_validate(card.body)
        report = next_report(scenario, attempt, now)
        if report is None:
            continue
        services = await training.load_services(session)
        service = services.get(scenario.service)
        title = service.title if service else scenario.service
        call, started = await officer.start_report(
            session,
            attempt,
            ts,
            card.version,
            scenario,
            report,
            title,
            telephony=live,
            now=now,
        )
        events.extend(started)
        if live:
            to_dial.append((attempt, call))
    return events, to_dial


async def dial_reports(to_dial: list[tuple[Attempt, dict]]) -> list[SessionEvent]:
    """Rings the trainee's phones for the reports just started; a report that could not be
    dialled is answered in text right away, as the officer's call is (issue #36)."""
    from app.db import SessionLocal
    from app.dialog import officer
    from app.telephony import service as telephony

    events: list[SessionEvent] = []
    manager = telephony.get_service()
    for attempt, call in to_dial:
        dialled = manager is not None and await manager.calls.dial_service(
            attempt.id, call["id"], call["service"], call.get("service_title") or ""
        )
        if dialled:
            continue
        async with SessionLocal() as fresh:
            current = await fresh.get(Attempt, attempt.id)
            if current is None:
                continue
            ts = await fresh.get(TrainingSession, current.session_id)
            card = await training.load_scenario_card(
                fresh, current.scenario_id, current.scenario_version
            )
            _, answered = await officer.answer(
                fresh,
                current,
                ts,
                card.version,
                CardResponseScenario.model_validate(card.body),
                call["id"],
            )
            await fresh.commit()
        events.extend(answered)
    return events
