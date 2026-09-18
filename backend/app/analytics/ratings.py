"""Skill ratings in the database: the row of (trainee, incident group, mode) moves after
every stored evaluation (called from ``training.service.evaluate_and_store``)."""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.analytics.rating import START_RATING, RatingState, incident_group, updated_rating
from app.models import Attempt, Scenario, SkillRating


async def apply_evaluation(session: AsyncSession, attempt: Attempt, total: float) -> SkillRating:
    """Moves the trainee's rating for the attempt's incident group and mode."""
    scenario = await session.get(Scenario, attempt.scenario_id)
    group = incident_group(scenario.incident_type_code if scenario else None)
    difficulty = scenario.difficulty if scenario else 2
    row = await session.get(SkillRating, (attempt.student_id, group, attempt.mode))
    if row is None:
        row = SkillRating(
            student_id=attempt.student_id,
            incident_group=group,
            mode=attempt.mode,
            rating=START_RATING,
            n=0,
        )
        session.add(row)
    row.rating = updated_rating(row.rating, difficulty, total)
    row.n += 1
    await session.flush()
    return row


async def load_state(session: AsyncSession, student_id: uuid.UUID) -> RatingState:
    """The stored ratings of one trainee as the in-memory state the domain works with."""
    state = RatingState()
    rows = await session.scalars(select(SkillRating).where(SkillRating.student_id == student_id))
    for r in rows:
        state.ratings[(r.incident_group, r.mode)] = r.rating
        state.counts[(r.incident_group, r.mode)] = r.n
    return state


async def load_states(
    session: AsyncSession, student_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, RatingState]:
    ids = list(student_ids)
    states = {i: RatingState() for i in ids}
    if not ids:
        return states
    rows = await session.scalars(select(SkillRating).where(SkillRating.student_id.in_(ids)))
    for r in rows:
        state = states[r.student_id]
        state.ratings[(r.incident_group, r.mode)] = r.rating
        state.counts[(r.incident_group, r.mode)] = r.n
    return states
