"""Adaptive card selection (PRD 9.7): the next card comes from the trainee's weakest
incident group, at a difficulty near the rating, unseen scenarios first."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Protocol

from app.domain.analytics.rating import RatingState, difficulty_rating, incident_group


class ScenarioLike(Protocol):
    id: uuid.UUID
    incident_type_code: str | None
    difficulty: int


def order_queue(
    scenarios: Sequence[ScenarioLike],
    *,
    mode: str,
    ratings: RatingState,
    done: Iterable[uuid.UUID] = (),
) -> list[ScenarioLike]:
    """The session queue reordered for one trainee. Sort keys, in order: not yet taken,
    lower rating of the incident group, difficulty closest to that rating, the original
    position (so equal candidates keep the teacher's order)."""
    seen = set(done)
    ranked = []
    for position, scenario in enumerate(scenarios):
        group = incident_group(scenario.incident_type_code)
        rating = ratings.get(group, mode)
        ranked.append(
            (
                scenario.id in seen,
                rating,
                abs(difficulty_rating(scenario.difficulty) - rating),
                position,
                scenario,
            )
        )
    ranked.sort(key=lambda item: item[:4])
    return [item[4] for item in ranked]
