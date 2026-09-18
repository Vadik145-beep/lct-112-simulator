"""Skill rating of a trainee per incident group and mode (PRD 9.7), Elo-like.

A scenario «plays» with the rating of its difficulty (1/2/3 → 1200/1400/1600); the trainee's
score is the evaluation total as a fraction. Beating a hard scenario therefore moves the rating
more than beating an easy one, and a poor result on an easy scenario costs more than on a hard
one.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

START_RATING = 1400.0
K_FACTOR = 32.0
DIFFICULTY_RATING: dict[int, float] = {1: 1200.0, 2: 1400.0, 3: 1600.0}
# A scenario without a classified incident type still counts, under this group.
UNKNOWN_GROUP = "0"


def incident_group(incident_type_code: str | None) -> str:
    """First segment of the classifier code («13.2.4.0» → «13»)."""
    if not incident_type_code:
        return UNKNOWN_GROUP
    head = str(incident_type_code).split(".")[0].strip()
    return head or UNKNOWN_GROUP


def difficulty_rating(difficulty: int) -> float:
    return DIFFICULTY_RATING.get(int(difficulty), DIFFICULTY_RATING[2])


def expected_score(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / 400.0))


def updated_rating(rating: float, difficulty: int, total: float) -> float:
    """Rating after an evaluated attempt with ``total`` points out of 100."""
    score = min(1.0, max(0.0, float(total) / 100.0))
    return rating + K_FACTOR * (score - expected_score(rating, difficulty_rating(difficulty)))


def nearest_difficulty(rating: float) -> int:
    """The difficulty whose rating is closest to the trainee's."""
    return min(DIFFICULTY_RATING, key=lambda d: abs(DIFFICULTY_RATING[d] - rating))


@dataclass
class RatingState:
    """In-memory ratings keyed by (incident_group, mode): the same replay the database
    rows go through, used by the simulator and the seed history."""

    ratings: dict[tuple[str, str], float]
    counts: dict[tuple[str, str], int]

    def __init__(self) -> None:
        self.ratings = {}
        self.counts = {}

    def get(self, group: str, mode: str) -> float:
        return self.ratings.get((group, mode), START_RATING)

    def apply(self, group: str, mode: str, difficulty: int, total: float) -> float:
        key = (group, mode)
        self.ratings[key] = updated_rating(self.get(group, mode), difficulty, total)
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.ratings[key]

    def minimum(self, mode: str | None = None) -> float | None:
        values = [r for (_, m), r in self.ratings.items() if mode is None or m == mode]
        return min(values) if values else None

    def weakest_groups(self, mode: str, groups: Iterable[str]) -> list[str]:
        """Groups ordered from the lowest rating; unseen groups count as the start rating."""
        return sorted(set(groups), key=lambda g: (self.get(g, mode), g))
