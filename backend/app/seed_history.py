"""Four weeks of demonstration history for «Учебная-1» (PRD 9.7, 15): finished sessions of
both modes with evaluated attempts whose spread looks like a real group, so the analytics,
the forecast and «Мой прогресс» have something to show on a fresh stand.

The outcomes come from the same generator as the model's training cohort
(``app.domain.analytics.simulate``); sessions are marked as demonstration data by their
``seed_key`` (``demo-history-…``) and title. Idempotent: nothing is added when the history
already exists. Called from ``app.seed``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import ratings
from app.domain.analytics import simulate
from app.domain.analytics.rating import incident_group
from app.domain.evaluation import call_intake, card_response
from app.domain.evaluation.timing import time_fraction
from app.intake.schemas import AddressIn, CallerIn, CardIn
from app.intake.service import card_json
from app.models import (
    ATTEMPT_EVALUATED,
    MODE_CALL_INTAKE,
    MODE_CARD_RESPONSE,
    SCENARIO_APPROVED,
    SESSION_FINISHED,
    Attempt,
    Evaluation,
    Group,
    GroupMember,
    Scenario,
    ScenarioVersion,
    TrainingSession,
    TypicalError,
    User,
)
from app.training import service as training

DEMO_HISTORY_PREFIX = "demo-history-"
DEMO_MARK = "демонстрационные данные"
GROUP_TITLE = "Учебная-1"
TEACHER_LOGIN = "teacher1"
WEEKS = 4
SEED = 112
# Hidden levels of student1…student6: a strong, an average and a struggling part of the group.
LEVELS = (1.0, 0.45, 0.15, -0.3, -0.75, -1.2)
ATTEMPTS_PER_SESSION = (2, 4)
NORM_SECONDS = {MODE_CARD_RESPONSE: 30, MODE_CALL_INTAKE: 60}
PASS_THRESHOLD = 70
MODE_TITLES = {
    MODE_CARD_RESPONSE: "Реагирование на карточку",
    MODE_CALL_INTAKE: "Приём вызова",
}
_CARD_NUMBER_BASE = 38250000


async def _scenarios(session: AsyncSession, mode: str) -> list[tuple[Scenario, dict]]:
    rows = list(
        await session.scalars(
            select(Scenario)
            .where(Scenario.kind == mode, Scenario.status == SCENARIO_APPROVED)
            .order_by(Scenario.created_at, Scenario.title)
        )
    )
    out = []
    for s in rows:
        version = await session.scalar(
            select(ScenarioVersion).where(
                ScenarioVersion.scenario_id == s.id, ScenarioVersion.version == s.current_version
            )
        )
        if version is not None:
            out.append((s, version.body))
    return out


def _components(mode: str, o: simulate.Outcome, errors: list[dict], norm: int) -> dict:
    """Component scores that add up to the generated total, with the items the review and
    the analytics read (time in seconds, decision, missing topics)."""
    if mode == MODE_CARD_RESPONSE:
        maxima = {
            k: v
            for k, v in card_response.DEFAULT_WEIGHTS.items()
            if k in card_response.applicable_components(None)
        }
        titles = card_response.TITLES
    else:
        maxima, titles = call_intake.DEFAULT_WEIGHTS, call_intake.TITLES
    fixed: dict[str, float] = {
        "time": round(maxima["time"] * time_fraction(o.seconds, norm), 1),
        "typical_errors": max(0.0, maxima["typical_errors"] - sum(e["penalty"] for e in errors)),
    }
    if mode == MODE_CARD_RESPONSE:
        fixed["decision"] = float(maxima["decision"]) if o.decision_correct else 0.0
    remaining = max(0.0, o.total - sum(fixed.values()))
    free = {k: v for k, v in maxima.items() if k not in fixed}
    free_max = sum(free.values())
    scores = dict(fixed)
    for key, mx in free.items():
        scores[key] = round(min(float(mx), remaining * mx / free_max), 1)
    items: dict[str, list[dict]] = {
        "time": [{"seconds": o.seconds, "norm_seconds": norm, "note": None}],
    }
    if mode == MODE_CARD_RESPONSE:
        items["decision"] = [
            {
                "expected": "accept",
                "actual": "accept" if o.decision_correct else "reject",
                "note": "решение верно" if o.decision_correct else "решение не совпало с эталоном",
            }
        ]
    else:
        required = ["what_happened", "address", "injured", "caller_name", "callback_phone"]
        missing = required[len(required) - o.topics_missed :] if o.topics_missed else []
        items["required_topics"] = [
            {
                "required": required,
                "covered": [t for t in required if t not in missing],
                "missing": missing,
            }
        ]
    return {
        key: {
            "key": key,
            "title": titles[key],
            "score": scores[key],
            "max": float(mx),
            "status": "checked",
            "items": items.get(key, []),
        }
        for key, mx in maxima.items()
    }


def _result(mode: str, o: simulate.Outcome, errors: list[dict], norm: int) -> dict:
    components = _components(mode, o, errors, norm)
    total = round(sum(c["score"] for c in components.values()))
    return {
        "mode": mode,
        "total": total,
        "passed": total >= PASS_THRESHOLD,
        "components": components,
        "errors": errors,
        "methods": {"grammar": "not_checked", "text": "tfidf"},
    }


def _status_log(o: simulate.Outcome, issued: datetime, primary: datetime) -> list[dict]:
    entries = [
        training._log_entry(training.STATUS_ADDED, at=issued, by=training.BY_SYSTEM),
        training._log_entry(
            training.STATUS_RECEIVED,
            at=issued + timedelta(seconds=min(5.0, o.seconds / 2)),
            by=training.BY_DISPATCHER,
        ),
    ]
    decision = "accepted" if o.decision_correct else training.STATUS_REJECTED
    entries.append(
        training._log_entry(
            decision,
            at=primary,
            by=training.BY_DISPATCHER,
            comment=None if o.decision_correct else "Не наш адрес",
            reject_reason=None if o.decision_correct else "not_our_territory",
        )
    )
    if o.decision_correct:
        entries.append(
            training._log_entry(
                training.STATUS_WORKS_DONE,
                at=primary + timedelta(minutes=25),
                by=training.BY_DISPATCHER,
                comment="Работы выполнены",
            )
        )
    return entries


def _call_draft(body: dict) -> dict:
    ref = body.get("reference_card") or {}
    address = {k: v for k, v in (ref.get("address") or {}).items() if k in AddressIn.model_fields}
    caller = {k: v for k, v in (ref.get("caller") or {}).items() if k in CallerIn.model_fields}
    card = CardIn(
        signs_path=list(ref.get("signs_path") or []),
        incident_type=ref.get("incident_type"),
        flags=dict(ref.get("flags") or {}),
        services=list(ref.get("expected_services") or [])[:3],
        address=AddressIn(**{k: str(v) for k, v in address.items()}),
        caller=CallerIn(**{k: str(v) for k, v in caller.items()}),
        description=str(ref.get("description") or ""),
    )
    return card_json(card)


async def seed_history(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Creates the history; returns the number of attempts added (0 when it exists)."""
    exists = await session.scalar(
        select(TrainingSession.id).where(TrainingSession.seed_key.like(f"{DEMO_HISTORY_PREFIX}%"))
    )
    if exists is not None:
        return 0
    teacher = await session.scalar(select(User).where(User.login == TEACHER_LOGIN))
    group = await session.scalar(select(Group).where(Group.title == GROUP_TITLE))
    if teacher is None or group is None:
        return 0
    members = list(
        await session.scalars(
            select(User)
            .join(GroupMember, GroupMember.student_id == User.id)
            .where(GroupMember.group_id == group.id)
            .order_by(User.login)
        )
    )
    scenarios = {m: await _scenarios(session, m) for m in MODE_TITLES}
    # The typical errors come from the dataset import; without them the history would have
    # no errors to show, so it waits for the next seed run.
    error_rows = {e.code: e for e in await session.scalars(select(TypicalError))}
    if not members or not all(scenarios.values()) or not error_rows:
        return 0

    rng = np.random.default_rng(SEED)
    trainees = {}
    for i, student in enumerate(members):
        t = simulate.random_trainee(rng)
        t.level = LEVELS[i % len(LEVELS)]
        trainees[student.id] = t
    index = dict.fromkeys(trainees, 0)

    now = now or datetime.now(UTC)
    start = (now - timedelta(weeks=WEEKS)).replace(hour=10, minute=0, second=0, microsecond=0)
    counter = 0
    added = 0
    for week in range(WEEKS):
        for day_offset, mode in ((1, MODE_CARD_RESPONSE), (3, MODE_CALL_INTAKE)):
            started = start + timedelta(weeks=week, days=day_offset)
            norm = NORM_SECONDS[mode]
            ts = TrainingSession(
                seed_key=f"{DEMO_HISTORY_PREFIX}w{week + 1}-{mode}",
                title=f"Неделя {week + 1}: {MODE_TITLES[mode].lower()} ({DEMO_MARK})",
                teacher_id=teacher.id,
                group_id=group.id,
                mode=mode,
                card_source="scenarios",
                scenario_ids=[s.id for s, _ in scenarios[mode]],
                difficulty=3,
                norm_seconds=norm,
                pass_threshold=PASS_THRESHOLD,
                hints_enabled=True,
                voice_enabled=True,
                dialog_mode="select",
                status=SESSION_FINISHED,
                started_at=started,
                finished_at=started + timedelta(hours=2),
            )
            session.add(ts)
            await session.flush()
            for student in members:
                trainee = trainees[student.id]
                count = int(rng.integers(ATTEMPTS_PER_SESSION[0], ATTEMPTS_PER_SESSION[1] + 1))
                picks = rng.choice(
                    len(scenarios[mode]), size=min(count, len(scenarios[mode])), replace=False
                )
                at = started + timedelta(minutes=int(rng.integers(0, 10)))
                for pick in picks:
                    scenario, body = scenarios[mode][int(pick)]
                    index[student.id] += 1
                    o = simulate.outcome(
                        rng,
                        trainee,
                        mode=mode,
                        group=incident_group(scenario.incident_type_code),
                        difficulty=scenario.difficulty,
                        index=index[student.id],
                    )
                    errors = [
                        {
                            "code": code,
                            "title": error_rows[code].title,
                            "penalty": error_rows[code].penalty,
                            "explanation": error_rows[code].description,
                            "memo_ref": error_rows[code].memo_ref,
                            "critical": False,
                        }
                        for code in o.errors
                        if code in error_rows
                    ]
                    result = _result(mode, o, errors, norm)
                    issued = at
                    answered = issued + timedelta(seconds=3)
                    primary = issued + timedelta(seconds=o.seconds)
                    # A card is closed long after the primary status, a call ends when the
                    # card is saved. The demo keeps the saved card the span the stored
                    # evaluation scored, so the report shows the same seconds
                    # (``training.attempt_seconds``).
                    submitted = (
                        primary + timedelta(minutes=25)
                        if mode == MODE_CARD_RESPONSE
                        else answered + timedelta(seconds=o.seconds)
                    )
                    counter += 1
                    attempt = Attempt(
                        session_id=ts.id,
                        student_id=student.id,
                        scenario_id=scenario.id,
                        scenario_version=scenario.current_version,
                        mode=mode,
                        card_number=str(_CARD_NUMBER_BASE + counter),
                        issued_at=issued,
                        received_at=issued + timedelta(seconds=3),
                        answered_at=answered if mode == MODE_CALL_INTAKE else None,
                        primary_status_at=primary if mode == MODE_CARD_RESPONSE else None,
                        submitted_at=submitted,
                        draft=_call_draft(body) if mode == MODE_CALL_INTAKE else None,
                        result=result,
                        status_log=(
                            _status_log(o, issued, primary) if mode == MODE_CARD_RESPONSE else []
                        ),
                        state=ATTEMPT_EVALUATED,
                        response_status=(
                            training.STATUS_WORKS_DONE
                            if mode == MODE_CARD_RESPONSE and o.decision_correct
                            else training.STATUS_REJECTED
                            if mode == MODE_CARD_RESPONSE
                            else training.STATUS_ADDED
                        ),
                        card_status=(
                            training.CARD_FINISHED
                            if mode == MODE_CARD_RESPONSE
                            else training.CARD_REGISTERED
                        ),
                        call_state="ended" if mode == MODE_CALL_INTAKE else "idle",
                        call_ended_at=submitted if mode == MODE_CALL_INTAKE else None,
                        call_end_reason="hangup" if mode == MODE_CALL_INTAKE else None,
                    )
                    session.add(attempt)
                    await session.flush()
                    session.add(
                        Evaluation(
                            attempt_id=attempt.id,
                            total=result["total"],
                            passed=result["passed"],
                            components=result["components"],
                            errors=result["errors"],
                            methods=result["methods"],
                            created_at=submitted,
                        )
                    )
                    await ratings.apply_evaluation(session, attempt, result["total"])
                    at = submitted + timedelta(minutes=int(rng.integers(2, 8)))
                    added += 1
    await session.flush()
    return added
