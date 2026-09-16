"""Time components: full points within the norm, zero at twice the norm, linear between
(PRD 9.2: norm 30 s → 30 s = 20, 45 s = 10, 60 s = 0)."""

from __future__ import annotations

from datetime import datetime


def time_fraction(seconds: float | None, norm_seconds: float) -> float:
    if seconds is None:
        return 0.0
    if norm_seconds <= 0:
        return 1.0 if seconds <= 0 else 0.0
    if seconds <= norm_seconds:
        return 1.0
    if seconds >= 2 * norm_seconds:
        return 0.0
    return 1.0 - (seconds - norm_seconds) / norm_seconds


def seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, (end - start).total_seconds())
