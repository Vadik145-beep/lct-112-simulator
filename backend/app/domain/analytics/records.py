"""One evaluated attempt as the analytics sees it, whatever its origin (database, simulator,
seed history). ``from_result`` reads the stored evaluation (``attempts.result``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.analytics.rating import incident_group


@dataclass(frozen=True)
class AttemptRecord:
    at: datetime
    mode: str
    incident_group: str
    difficulty: int
    total: float
    passed: bool
    # Time to the primary status (card) or to saving the card (call); None when not reached.
    seconds: float | None
    norm_seconds: int
    # Card response only: was the accept / reject decision right. None for calls.
    decision_correct: bool | None
    # Call intake only: required topics the operator never asked.
    topics_missed: int
    errors: tuple[str, ...] = field(default_factory=tuple)
    session_id: str | None = None
    attempt_id: str | None = None

    @property
    def time_ratio(self) -> float:
        """Time as a share of the norm; a missing time counts as twice the norm (zero points
        in the evaluation)."""
        if self.norm_seconds <= 0:
            return 1.0
        if self.seconds is None:
            return 2.0
        return self.seconds / self.norm_seconds


def from_result(
    *,
    at: datetime,
    mode: str,
    incident_type_code: str | None,
    difficulty: int,
    norm_seconds: int,
    result: dict[str, Any] | None,
    session_id: str | None = None,
    attempt_id: str | None = None,
) -> AttemptRecord | None:
    """The record of a stored evaluation; None when the attempt has no result yet."""
    if not result:
        return None
    components = result.get("components") or {}
    time_items = (components.get("time") or {}).get("items") or []
    seconds = time_items[0].get("seconds") if time_items else None
    decision_items = (components.get("decision") or {}).get("items") or []
    decision_correct: bool | None = None
    if decision_items:
        first = decision_items[0]
        expected, actual = first.get("expected"), first.get("actual")
        decision_correct = expected is not None and expected == actual
    topic_items = (components.get("required_topics") or {}).get("items") or []
    topics_missed = len(topic_items[0].get("missing") or []) if topic_items else 0
    return AttemptRecord(
        at=at,
        mode=mode,
        incident_group=incident_group(incident_type_code),
        difficulty=int(difficulty),
        total=float(result.get("total") or 0.0),
        passed=bool(result.get("passed")),
        seconds=None if seconds is None else float(seconds),
        norm_seconds=int(norm_seconds),
        decision_correct=decision_correct,
        topics_missed=topics_missed,
        errors=tuple(str(e.get("code")) for e in result.get("errors") or [] if e.get("code")),
        session_id=session_id,
        attempt_id=attempt_id,
    )
