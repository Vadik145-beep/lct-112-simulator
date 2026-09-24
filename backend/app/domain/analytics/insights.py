"""Group insights and trainee recommendations from attempt records: heat map cells, dynamics
by week, the top typical errors and the template summary (PRD 9.7)."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.analytics.rating import RatingState
from app.domain.analytics.records import AttemptRecord

TOP_ERRORS = 5
WEEK = timedelta(days=7)


@dataclass(frozen=True)
class ErrorCount:
    code: str
    title: str
    count: int
    students: int
    students_share: float


def top_errors(
    by_student: Mapping[str, Sequence[AttemptRecord]],
    titles: Mapping[str, str],
    limit: int = TOP_ERRORS,
) -> list[ErrorCount]:
    counts: Counter[str] = Counter()
    who: dict[str, set[str]] = defaultdict(set)
    for student, records in by_student.items():
        for r in records:
            for code in r.errors:
                counts[code] += 1
                who[code].add(student)
    total_students = len(by_student) or 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return [
        ErrorCount(
            code=code,
            title=titles.get(code, code),
            count=count,
            students=len(who[code]),
            students_share=round(len(who[code]) / total_students, 2),
        )
        for code, count in ordered
    ]


@dataclass(frozen=True)
class Cell:
    student: str
    incident_group: str
    mode: str
    mean: float
    count: int


def heatmap(by_student: Mapping[str, Sequence[AttemptRecord]]) -> list[Cell]:
    cells: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for student, records in by_student.items():
        for r in records:
            cells[(student, r.incident_group, r.mode)].append(r.total)
    return [
        Cell(student=s, incident_group=g, mode=m, mean=round(sum(v) / len(v), 1), count=len(v))
        for (s, g, m), v in sorted(cells.items())
    ]


@dataclass(frozen=True)
class WeekPoint:
    week_start: datetime
    mode: str
    mean_score: float
    mean_time_ratio: float
    count: int


def dynamics(records: Sequence[AttemptRecord], *, since: datetime) -> list[WeekPoint]:
    """Mean score and time (share of the norm) per week and mode."""
    buckets: dict[tuple[datetime, str], list[AttemptRecord]] = defaultdict(list)
    for r in records:
        if r.at < since:
            continue
        index = int((r.at - since) // WEEK)
        buckets[(since + index * WEEK, r.mode)].append(r)
    return [
        WeekPoint(
            week_start=start,
            mode=mode,
            mean_score=round(sum(r.total for r in rs) / len(rs), 1),
            mean_time_ratio=round(sum(r.time_ratio for r in rs) / len(rs), 2),
            count=len(rs),
        )
        for (start, mode), rs in sorted(buckets.items())
    ]


MODE_TITLES = {"call_intake": "приёме вызова", "card_response": "реагировании на карточку"}


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian declension after a number: 1 попытка, 2 попытки, 5 попыток."""
    tens, unit = n % 100, n % 10
    if 11 <= tens <= 14:
        return many
    if unit == 1:
        return one
    if 2 <= unit <= 4:
        return few
    return many


def group_summary(
    *,
    students: int,
    attempts: int,
    mean_score: float | None,
    weakest_group: tuple[str, str, float] | None,
    errors: Sequence[ErrorCount],
    at_risk: int,
) -> str:
    """The template summary (the model-written one is optional, PRD 9.7). ``students`` counts
    the trainees with attempts in the period, ``weakest_group`` is the incident group and the
    mode of the weakest cell of the heat map, so the two never disagree."""
    if not attempts:
        return "Оценённых попыток за период нет: аналитика появится после первого занятия."
    who = plural(students, "обучающегося", "обучающихся", "обучающихся")
    tries = plural(attempts, "оценённая попытка", "оценённые попытки", "оценённых попыток")
    parts = [f"За период {attempts} {tries} у {students} {who}"]
    if mean_score is not None:
        parts[0] += f", средний балл {mean_score:.0f}"
    parts[0] += "."
    if weakest_group:
        title, mode, mean = weakest_group
        where = MODE_TITLES.get(mode, mode)
        parts.append(f"Слабее всего «{title}» в {where}: средний балл {mean:.0f}.")
    if errors:
        top = errors[0]
        parts.append(
            f"Самая частая ошибка — «{top.title}»: {top.count} "
            f"{plural(top.count, 'раз', 'раза', 'раз')} "
            f"у {top.students} из {students} {who}."
        )
    if at_risk:
        parts.append(
            f"Под риском не пройти аттестацию: {at_risk}. Стоит дать им карточки по слабым группам."
        )
    else:
        parts.append("Обучающихся с высоким риском нет.")
    return " ".join(parts)


def weakest_group_tip(ratings: RatingState, group_titles: Mapping[str, str]) -> str | None:
    """The advice about the lowest-rated incident group; None without ratings."""
    weakest = sorted(ratings.ratings.items(), key=lambda kv: kv[1])
    if not weakest:
        return None
    (group, mode), rating = weakest[0]
    title = group_titles.get(group, f"группа {group}")
    mode_title = "приём вызова" if mode == "call_intake" else "карточки"
    return (
        f"Слабое место — «{title}» ({mode_title}), рейтинг {rating:.0f}: "
        "возьмите карточки этой группы на следующем занятии."
    )


def recommendations(
    records: Sequence[AttemptRecord],
    ratings: RatingState,
    group_titles: Mapping[str, str],
    error_titles: Mapping[str, str],
) -> list[str]:
    """Two or three practical advices for a trainee from the weakest group, the slowest
    mode and the most frequent error."""
    if not records:
        return ["Пройдите первое занятие — рекомендации появятся после оценки попыток."]
    out: list[str] = []
    tip = weakest_group_tip(ratings, group_titles)
    if tip:
        out.append(tip)
    slow = [r for r in records if r.time_ratio > 1.0]
    if len(slow) >= max(2, len(records) // 3):
        out.append(
            f"В {len(slow)} из {len(records)} попыток время выше норматива: "
            "сначала первичный статус или адрес, подробности потом."
        )
    errors = Counter(code for r in records for code in r.errors)
    if errors:
        code, count = errors.most_common(1)[0]
        out.append(
            f"Чаще всего повторяется ошибка «{error_titles.get(code, code)}» ({count}): "
            "перечитайте соответствующий раздел памятки."
        )
    return out[:3]
