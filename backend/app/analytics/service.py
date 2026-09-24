"""Builds the analytics responses from stored evaluations: attempt records per trainee for a
period, the heat map, dynamics, typical errors, the readiness forecast and the trainee's own
progress."""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics import ratings as rating_rows
from app.analytics.schemas import (
    CalibrationBucketOut,
    ErrorCountOut,
    FeatureWeightOut,
    GroupAnalyticsOut,
    HeatCellOut,
    IncidentGroupOut,
    PeriodOut,
    PersonOut,
    RatingOut,
    ReadinessModelOut,
    ReadinessOut,
    VolumeOut,
    WeekPointOut,
)
from app.config import get_settings
from app.domain.analytics import insights
from app.domain.analytics.features import FEATURE_TITLES, compute_features
from app.domain.analytics.model import ReadinessModel, load_metrics
from app.domain.analytics.rating import RatingState
from app.domain.analytics.records import AttemptRecord, from_result
from app.models import (
    Attempt,
    Group,
    IncidentGroup,
    Scenario,
    TrainingSession,
    TypicalError,
    User,
)
from app.training import sessions as lessons

DEFAULT_PERIOD_DAYS = 28
MAX_PERIOD_DAYS = 365
# Seed keys of the demonstration history (app.seed_history) start with this.
DEMO_HISTORY_PREFIX = "demo-history-"


def models_dir() -> Path:
    return Path(get_settings().analytics_dir) / "models"


@lru_cache(maxsize=1)
def _cached_model(folder: str, mtime: float) -> ReadinessModel | None:
    return ReadinessModel.load(Path(folder))


def load_model() -> ReadinessModel | None:
    """The trained forecast model, re-read when the file changes."""
    folder = models_dir()
    path = folder / "readiness_model.json"
    mtime = path.stat().st_mtime if path.exists() else 0.0
    return _cached_model(str(folder), mtime)


# ------------------------------------------------------------------ loading records


@dataclass
class History:
    """Evaluated attempts of several trainees with the sessions they came from."""

    records: dict[uuid.UUID, list[AttemptRecord]]
    sessions: dict[uuid.UUID, TrainingSession]
    demo_data: bool

    def all_records(self) -> list[AttemptRecord]:
        return [r for rs in self.records.values() for r in rs]


async def load_history(
    session: AsyncSession, student_ids: list[uuid.UUID], *, since: datetime | None = None
) -> History:
    ids = list(student_ids)
    records: dict[uuid.UUID, list[AttemptRecord]] = {i: [] for i in ids}
    if not ids:
        return History(records=records, sessions={}, demo_data=False)
    query = (
        select(Attempt, Scenario, TrainingSession)
        .join(Scenario, Scenario.id == Attempt.scenario_id)
        .join(TrainingSession, TrainingSession.id == Attempt.session_id)
        .where(Attempt.student_id.in_(ids), Attempt.result.is_not(None))
        .order_by(Attempt.issued_at)
    )
    if since is not None:
        query = query.where(Attempt.issued_at >= since)
    sessions: dict[uuid.UUID, TrainingSession] = {}
    demo = False
    for attempt, scenario, ts in await session.execute(query):
        sessions[ts.id] = ts
        demo = demo or bool(ts.seed_key and ts.seed_key.startswith(DEMO_HISTORY_PREFIX))
        record = from_result(
            at=attempt.submitted_at or attempt.issued_at,
            mode=attempt.mode,
            incident_type_code=scenario.incident_type_code,
            difficulty=scenario.difficulty,
            norm_seconds=ts.norm_seconds,
            result=attempt.result,
            session_id=str(ts.id),
            attempt_id=str(attempt.id),
        )
        if record is not None:
            records[attempt.student_id].append(record)
    return History(records=records, sessions=sessions, demo_data=demo)


async def _titles(session: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    groups = {g.code: g.title for g in await session.scalars(select(IncidentGroup))}
    errors = {e.code: e.title for e in await session.scalars(select(TypicalError))}
    return groups, errors


# ------------------------------------------------------------------ group analytics


def _forecast(
    model: ReadinessModel | None, records: list[AttemptRecord], state: RatingState
) -> tuple[float | None, str, list[str]]:
    if not records:
        return None, "unknown", ["Оценённых попыток за период нет."]
    if model is None:
        return None, "unknown", ["Модель прогноза не обучена: см. «Достоверность прогноза»."]
    prediction = model.predict_one(compute_features(records, state))
    return prediction.probability, prediction.risk, prediction.reasons


async def group_analytics(
    session: AsyncSession, group: Group, *, days: int = DEFAULT_PERIOD_DAYS
) -> GroupAnalyticsOut:
    days = max(1, min(int(days), MAX_PERIOD_DAYS))
    until = datetime.now(UTC)
    since = (until - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    members = (await lessons.group_members(session, [group.id])).get(group.id, [])
    ids = [m.id for m in members]
    # The forecast looks at the whole history of a trainee; the charts at the period.
    history = await load_history(session, ids)
    states = await rating_rows.load_states(session, ids)
    group_titles, error_titles = await _titles(session)
    model = load_model()

    by_login = {str(m.id): [r for r in history.records[m.id] if r.at >= since] for m in members}
    all_records = [r for rs in by_login.values() for r in rs]
    period_sessions = {r.session_id for r in all_records}
    cells = insights.heatmap(by_login)
    present_groups = sorted(
        {r.incident_group for r in all_records}, key=lambda c: int(c) if c.isdigit() else 999
    )
    errors = insights.top_errors(by_login, error_titles)

    readiness: list[ReadinessOut] = []
    for m in members:
        probability, risk, reasons = _forecast(model, history.records[m.id], states[m.id])
        readiness.append(
            ReadinessOut(
                student_id=m.id,
                full_name=m.full_name,
                attempts=len(history.records[m.id]),
                probability=probability,
                risk=risk,
                reasons=reasons,
            )
        )

    # The weakest cell of the heat map, group and mode together: a group averaged over both
    # modes would name a number the teacher cannot find anywhere on the page.
    weakest: tuple[str, str, float] | None = None
    by_group: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in all_records:
        by_group[(r.incident_group, r.mode)].append(r.total)
    if by_group:
        (code, mode), totals = min(by_group.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        weakest = (group_titles.get(code, f"группа {code}"), mode, sum(totals) / len(totals))
    mean_score = (
        round(sum(r.total for r in all_records) / len(all_records), 1) if all_records else None
    )
    summary = insights.group_summary(
        students=sum(1 for m in members if by_login[str(m.id)]),
        attempts=len(all_records),
        mean_score=mean_score,
        weakest_group=weakest,
        errors=errors,
        at_risk=sum(1 for r in readiness if r.risk == "high"),
    )
    return GroupAnalyticsOut(
        group_id=group.id,
        group_title=group.title,
        period=PeriodOut(since=since, until=until, days=days),
        demo_data=history.demo_data,
        volume=VolumeOut(
            students=len(members),
            students_with_attempts=sum(1 for m in members if by_login[str(m.id)]),
            attempts=len(all_records),
            sessions=len(period_sessions),
        ),
        students=[PersonOut(id=m.id, login=m.login, full_name=m.full_name) for m in members],
        incident_groups=[
            IncidentGroupOut(code=c, title=group_titles.get(c, f"Группа {c}"))
            for c in present_groups
        ],
        heatmap=[
            HeatCellOut(
                student_id=uuid.UUID(c.student),
                incident_group=c.incident_group,
                mode=c.mode,
                mean=c.mean,
                count=c.count,
            )
            for c in cells
        ],
        dynamics=[WeekPointOut(**p.__dict__) for p in insights.dynamics(all_records, since=since)],
        errors=[ErrorCountOut(**e.__dict__) for e in errors],
        readiness=readiness,
        model_available=model is not None,
        summary=summary,
    )


# ------------------------------------------------------------------ model card


def readiness_model_info() -> ReadinessModelOut:
    metrics = load_metrics(models_dir())
    if not metrics:
        return ReadinessModelOut(trained=False)
    return ReadinessModelOut(
        trained=True,
        trained_at=datetime.fromisoformat(metrics["trained_at"]),
        n_total=metrics["n_total"],
        n_train=metrics["n_train"],
        n_test=metrics["n_test"],
        positive_share_test=metrics["positive_share_test"],
        roc_auc=metrics["roc_auc"],
        brier=metrics["brier"],
        brier_baseline=metrics["brier_baseline"],
        calibration=[
            CalibrationBucketOut(
                lower=b["from"],
                upper=b["to"],
                predicted=b["predicted"],
                observed=b["observed"],
                count=b["count"],
            )
            for b in metrics["calibration"]
        ],
        features=[
            FeatureWeightOut(
                name=f["name"], title=FEATURE_TITLES.get(f["name"], f["name"]), weight=f["weight"]
            )
            for f in metrics["features"]
        ],
    )


# ------------------------------------------------------------------ trainee progress


@dataclass
class ProgressExtras:
    """What the analytics adds to «Мой прогресс» (app.training.progress): ratings by
    incident group and mode, weekly dynamics, the demonstration mark and the tip about the
    weakest group."""

    ratings: list[RatingOut]
    dynamics: list[WeekPointOut]
    demo_data: bool
    weakest_tip: str | None


async def progress_extras(session: AsyncSession, student: User) -> ProgressExtras:
    history = await load_history(session, [student.id])
    records = history.records[student.id]
    state = await rating_rows.load_state(session, student.id)
    group_titles, _ = await _titles(session)
    since = (
        min(r.at for r in records).replace(hour=0, minute=0, second=0, microsecond=0)
        if records
        else datetime.now(UTC)
    )
    return ProgressExtras(
        ratings=[
            RatingOut(
                incident_group=g,
                title=group_titles.get(g, f"Группа {g}"),
                mode=m,
                rating=round(v, 0),
                n=state.counts.get((g, m), 0),
            )
            for (g, m), v in sorted(state.ratings.items(), key=lambda kv: kv[1])
        ],
        dynamics=[WeekPointOut(**p.__dict__) for p in insights.dynamics(records, since=since)],
        demo_data=history.demo_data,
        weakest_tip=insights.weakest_group_tip(state, group_titles) if records else None,
    )
