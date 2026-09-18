"""Features of the readiness forecast (PRD 9.7) computed from a trainee's attempt history,
and the phrases that explain a forecast by its two most influential features."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.analytics.rating import START_RATING, RatingState
from app.domain.analytics.records import AttemptRecord

# The window of «recent» attempts the features look at.
RECENT = 10

FEATURE_NAMES: tuple[str, ...] = (
    "mean_recent",
    "trend",
    "time_ratio",
    "wrong_decisions",
    "topics_missed",
    "errors_per_attempt",
    "min_rating",
    "attempts",
)

FEATURE_TITLES: dict[str, str] = {
    "mean_recent": "Средний балл последних попыток",
    "trend": "Динамика балла",
    "time_ratio": "Время относительно норматива",
    "wrong_decisions": "Доля неверных решений",
    "topics_missed": "Пропущенные обязательные вопросы",
    "errors_per_attempt": "Типичных ошибок на попытку",
    "min_rating": "Самая слабая группа происшествий",
    "attempts": "Число попыток",
}


@dataclass(frozen=True)
class Features:
    values: dict[str, float]

    def vector(self) -> list[float]:
        return [self.values[name] for name in FEATURE_NAMES]


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def replay_ratings(records: Sequence[AttemptRecord]) -> RatingState:
    """Ratings after every attempt in time order (how the database rows were built)."""
    state = RatingState()
    for r in sorted(records, key=lambda x: x.at):
        state.apply(r.incident_group, r.mode, r.difficulty, r.total)
    return state


def compute_features(
    records: Sequence[AttemptRecord], ratings: RatingState | None = None
) -> Features:
    """Features from the whole history; the recent window drives most of them."""
    ordered = sorted(records, key=lambda x: x.at)
    recent = ordered[-RECENT:]
    state = ratings if ratings is not None else replay_ratings(ordered)
    totals = [r.total for r in recent]
    if len(totals) >= 4:
        half = len(totals) // 2
        trend = _mean(totals[half:]) - _mean(totals[:half])
    else:
        trend = 0.0
    decisions = [r.decision_correct for r in recent if r.decision_correct is not None]
    calls = [r for r in recent if r.mode == "call_intake"]
    min_rating = state.minimum()
    values = {
        "mean_recent": _mean(totals),
        "trend": trend,
        "time_ratio": _mean([r.time_ratio for r in recent]) if recent else 1.0,
        "wrong_decisions": (
            sum(1 for d in decisions if not d) / len(decisions) if decisions else 0.0
        ),
        "topics_missed": _mean([float(r.topics_missed) for r in calls]) if calls else 0.0,
        "errors_per_attempt": _mean([float(len(r.errors)) for r in recent]) if recent else 0.0,
        "min_rating": min_rating if min_rating is not None else START_RATING,
        "attempts": float(len(ordered)),
    }
    return Features(values=values)


def explain(name: str, value: float, favourable: bool) -> str:
    """One phrase about a feature: what it is and whether it helps or hurts the forecast."""
    if name == "mean_recent":
        return f"средний балл последних попыток {value:.0f}" + (
            " — уверенный уровень" if favourable else " — ниже, чем нужно для зачёта"
        )
    if name == "trend":
        if abs(value) < 1:
            return "балл держится на одном уровне"
        return f"балл {'растёт' if value > 0 else 'падает'}: {value:+.0f} за последние попытки"
    if name == "time_ratio":
        pct = value * 100
        return f"время {pct:.0f} % от норматива" + (
            " — укладывается" if favourable else " — выходит за норматив"
        )
    if name == "wrong_decisions":
        pct = value * 100
        return (f"неверных решений {pct:.0f} %" if pct >= 1 else "решения по карточкам верные") + (
            "" if favourable else " — решения по карточкам подводят"
        )
    if name == "topics_missed":
        return (
            f"пропускает {value:.1f} обязательных вопроса за вызов"
            if value >= 0.05
            else "обязательные вопросы задаёт"
        )
    if name == "errors_per_attempt":
        return (
            f"{value:.1f} типичных ошибки на попытку"
            if value >= 0.05
            else "типичных ошибок почти нет"
        )
    if name == "min_rating":
        return f"рейтинг самой слабой группы происшествий {value:.0f}" + (
            "" if favourable else " — есть провал по одной из групп"
        )
    if name == "attempts":
        n = int(value)
        return f"{n} попыток в истории" + ("" if favourable else " — мало данных для уверенности")
    return f"{FEATURE_TITLES.get(name, name)}: {value:.2f}"
