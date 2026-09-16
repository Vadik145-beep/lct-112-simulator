"""Evaluation result structure, weights of a training session and renormalization.

``components`` carry ``score``/``max`` per component plus ``items`` explaining the score;
``errors`` are the typical errors that fired; ``methods`` say how text was checked
(«languagetool» or «not_checked», «e5-small» or «tfidf»). A component is ``not_checked`` when
its provider is unavailable: it is excluded and the total is renormalized to 100. A component
with weight 0 is ``disabled`` and excluded the same way.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

ComponentStatus = Literal["checked", "not_checked", "disabled"]
DEFAULT_PASS_THRESHOLD = 70


@dataclass
class Component:
    key: str
    title: str
    score: float
    max: float
    status: ComponentStatus = "checked"
    items: list[dict[str, Any]] = field(default_factory=list)

    def counted(self) -> bool:
        return self.status == "checked"


@dataclass
class ErrorItem:
    code: str
    title: str
    penalty: int
    explanation: str
    memo_ref: str | None = None
    critical: bool = False


@dataclass
class EvaluationResult:
    mode: str
    total: int
    passed: bool
    components: dict[str, Component]
    errors: list[ErrorItem]
    methods: dict[str, str]
    max_total: int = 100

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "total": self.total,
            "passed": self.passed,
            "components": {k: asdict(c) for k, c in self.components.items()},
            "errors": [asdict(e) for e in self.errors],
            "methods": dict(self.methods),
        }


def apply_weights(defaults: Mapping[str, int], weights: Mapping[str, int] | None) -> dict[str, int]:
    """Maximums per component for a training session. ``weights`` overrides the defaults; the
    result must sum to 100 (a session with other sums is rejected when it is created)."""
    result = dict(defaults)
    if weights:
        unknown = set(weights) - set(defaults)
        if unknown:
            raise ValueError(f"неизвестные составляющие оценки: {sorted(unknown)}")
        for key, value in weights.items():
            if value < 0:
                raise ValueError(f"вес составляющей «{key}» не может быть отрицательным")
            result[key] = int(value)
    if sum(result.values()) != 100:
        raise ValueError(f"сумма весов должна быть 100, получено {sum(result.values())}")
    return result


def scale(max_points: float, fraction: float) -> float:
    """``fraction`` of ``max_points`` rounded to one decimal, never below 0 or above max."""
    value = max(0.0, min(1.0, fraction)) * max_points
    return round(value, 1)


def finalize(
    mode: str,
    components: list[Component],
    errors: list[ErrorItem],
    methods: Mapping[str, str],
    pass_threshold: int = DEFAULT_PASS_THRESHOLD,
) -> EvaluationResult:
    """Total on a 0..100 scale over the counted components; passed when the total reaches the
    threshold and no critical error fired."""
    counted = [c for c in components if c.counted()]
    max_sum = sum(c.max for c in counted)
    if max_sum > 0:
        total = round(sum(c.score for c in counted) / max_sum * 100)
    else:
        total = 0
    total = max(0, min(100, int(total)))
    passed = total >= pass_threshold and not any(e.critical for e in errors)
    return EvaluationResult(
        mode=mode,
        total=total,
        passed=passed,
        components={c.key: c for c in components},
        errors=errors,
        methods=dict(methods),
    )
