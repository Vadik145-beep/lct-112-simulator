"""Virtual cohort for training and checking the readiness forecast (PRD 9.7).

Every virtual trainee has a hidden level, per-group strengths, a learning rate and a noise
level. The history is a run of attempts with noisy outcomes; the label («passed the
certification») comes from 20 further attempts that the features never see. The same outcome
generator (``Outcome``) drives the seed history of «Учебная-1», so demo data and the model's
training data follow one set of rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import numpy as np

from app.domain.analytics.records import AttemptRecord

MODE_CARD = "card_response"
MODE_CALL = "call_intake"
NORM_SECONDS = {MODE_CARD: 30, MODE_CALL: 60}
# Incident groups the seed scenarios cover: fire, road accident, gas, disturbance, crime, medical.
GROUPS: tuple[str, ...] = ("1", "2", "13", "15", "17", "22")
ERROR_CODES = {
    MODE_CARD: (
        "late_primary",
        "status_mismatch",
        "empty_reject_comment",
        "incomplete_comment",
        "progress_missing",
        "wrong_final_status",
        "profile_refusal",
    ),
    MODE_CALL: ("address_not_asked", "region_not_clarified", "no_call_dropped_mark"),
}
PASS_THRESHOLD = 70.0
CERTIFICATION_ATTEMPTS = 20
# Points of score per unit of latent skill and the score of an average trainee at difficulty 2.
SCORE_PER_LATENT = 12.0
SCORE_AVERAGE = 65.0


@dataclass
class Trainee:
    level: float
    group_offsets: dict[str, float]
    learning_rate: float
    noise: float
    slowness: float
    # How the trainee performs on the certification day compared with training (nerves,
    # form): the part of the outcome no history can predict.
    exam_shift: float = 0.0

    def latent(self, group: str, difficulty: int, index: int) -> float:
        return (
            self.level
            + self.group_offsets.get(group, 0.0)
            + self.learning_rate * index
            - 0.35 * (difficulty - 2)
        )


@dataclass(frozen=True)
class Outcome:
    total: float
    passed: bool
    seconds: float
    decision_correct: bool | None
    topics_missed: int
    errors: tuple[str, ...]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def random_trainee(rng: np.random.Generator) -> Trainee:
    return Trainee(
        level=float(rng.normal(0.0, 1.0)),
        group_offsets={g: float(rng.normal(0.0, 0.5)) for g in GROUPS},
        learning_rate=float(rng.uniform(0.002, 0.02)),
        noise=float(rng.uniform(6.0, 18.0)),
        slowness=float(rng.normal(1.0, 0.15)),
        exam_shift=float(rng.normal(0.0, 0.45)),
    )


def outcome(
    rng: np.random.Generator,
    trainee: Trainee,
    *,
    mode: str,
    group: str,
    difficulty: int,
    index: int,
    exam: bool = False,
) -> Outcome:
    """Noisy result of one attempt of the trainee."""
    latent = trainee.latent(group, difficulty, index) + (trainee.exam_shift if exam else 0.0)
    expected = SCORE_AVERAGE + SCORE_PER_LATENT * latent
    total = float(np.clip(rng.normal(expected, trainee.noise), 0, 100))
    ratio = float(np.clip(rng.normal(trainee.slowness - 0.15 * latent, 0.2), 0.3, 2.5))
    seconds = round(ratio * NORM_SECONDS[mode], 1)
    decision_correct: bool | None = None
    topics_missed = 0
    if mode == MODE_CARD:
        decision_correct = bool(rng.random() < _sigmoid(1.5 + 1.2 * latent))
    else:
        topics_missed = int(rng.poisson(max(0.0, 0.8 - 0.6 * latent)))
    n_errors = int(rng.poisson(max(0.05, 0.9 - 0.5 * latent)))
    codes = ERROR_CODES[mode]
    errors = tuple(rng.choice(codes, size=min(n_errors, len(codes)), replace=False).tolist())
    return Outcome(
        total=round(total, 1),
        passed=total >= PASS_THRESHOLD,
        seconds=seconds,
        decision_correct=decision_correct,
        topics_missed=topics_missed,
        errors=errors,
    )


def _record(at: datetime, mode: str, group: str, difficulty: int, o: Outcome) -> AttemptRecord:
    return AttemptRecord(
        at=at,
        mode=mode,
        incident_group=group,
        difficulty=difficulty,
        total=o.total,
        passed=o.passed,
        seconds=o.seconds,
        norm_seconds=NORM_SECONDS[mode],
        decision_correct=o.decision_correct,
        topics_missed=o.topics_missed,
        errors=o.errors,
    )


@dataclass
class VirtualStudent:
    trainee: Trainee
    history: list[AttemptRecord] = field(default_factory=list)
    certification: list[AttemptRecord] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        """Passed the certification: the mean of the held-out attempts reaches the threshold."""
        totals = [r.total for r in self.certification]
        return bool(totals) and sum(totals) / len(totals) >= PASS_THRESHOLD


def simulate_student(
    rng: np.random.Generator, *, history_length: int, start: datetime | None = None
) -> VirtualStudent:
    trainee = random_trainee(rng)
    start = start or datetime(2026, 8, 1, tzinfo=UTC)
    student = VirtualStudent(trainee=trainee)
    at = start
    for i in range(history_length + CERTIFICATION_ATTEMPTS):
        mode = MODE_CARD if rng.random() < 0.5 else MODE_CALL
        group = str(rng.choice(GROUPS))
        difficulty = int(rng.choice([1, 2, 3], p=[0.3, 0.45, 0.25]))
        at = at + timedelta(hours=float(rng.uniform(2, 30)))
        exam = i >= history_length
        result = outcome(
            rng, trainee, mode=mode, group=group, difficulty=difficulty, index=i, exam=exam
        )
        record = _record(at, mode, group, difficulty, result)
        (student.history if i < history_length else student.certification).append(record)
    return student


def simulate_cohort(size: int = 300, *, seed: int = 20260924) -> list[VirtualStudent]:
    rng = np.random.default_rng(seed)
    return [simulate_student(rng, history_length=int(rng.integers(5, 61))) for _ in range(size)]
