"""Group insights: heat map cells, weekly dynamics, top errors, template texts."""

from datetime import UTC, datetime, timedelta

from app.domain.analytics import insights
from app.domain.analytics.rating import RatingState
from app.domain.analytics.records import AttemptRecord

SINCE = datetime(2026, 9, 1, tzinfo=UTC)


def record(day: int, total: float, group: str = "1", mode: str = "card_response", **kw):
    base = {
        "at": SINCE + timedelta(days=day),
        "mode": mode,
        "incident_group": group,
        "difficulty": 2,
        "total": total,
        "passed": total >= 70,
        "seconds": 30.0,
        "norm_seconds": 30,
        "decision_correct": None,
        "topics_missed": 0,
        "errors": (),
    }
    return AttemptRecord(**{**base, **kw})


def test_heatmap_and_dynamics() -> None:
    by_student = {
        "a": [record(0, 80), record(1, 60), record(8, 90, group="13")],
        "b": [record(2, 40, mode="call_intake")],
    }
    cells = insights.heatmap(by_student)
    cell = next(c for c in cells if c.student == "a" and c.incident_group == "1")
    assert cell.mean == 70 and cell.count == 2
    weeks = insights.dynamics([r for rs in by_student.values() for r in rs], since=SINCE)
    assert [(w.week_start.day, w.mode, w.count) for w in weeks] == [
        (1, "call_intake", 1),
        (1, "card_response", 2),
        (8, "card_response", 1),
    ]


def test_top_errors_count_students() -> None:
    by_student = {
        "a": [record(0, 50, errors=("late_primary", "status_mismatch"))],
        "b": [record(0, 50, errors=("late_primary",))],
        "c": [record(0, 90)],
    }
    top = insights.top_errors(by_student, {"late_primary": "Поздний статус"})
    assert top[0].code == "late_primary" and top[0].title == "Поздний статус"
    assert top[0].count == 2 and top[0].students == 2 and top[0].students_share == 0.67
    assert top[1].code == "status_mismatch" and top[1].title == "status_mismatch"


def test_summary_and_recommendations() -> None:
    empty = insights.group_summary(
        students=3, attempts=0, mean_score=None, weakest_group=None, errors=[], at_risk=0
    )
    assert "нет" in empty
    text = insights.group_summary(
        students=3,
        attempts=12,
        mean_score=66.4,
        weakest_group=("Утечка газа", 51.0),
        errors=insights.top_errors({"a": [record(0, 50, errors=("x",))]}, {"x": "Ошибка X"}),
        at_risk=2,
    )
    assert "12 оценённых попыток" in text and "Утечка газа" in text and "Ошибка X" in text
    assert "риском" in text

    state = RatingState()
    state.apply("13", "card_response", 2, 10)
    records = [record(i, 40, seconds=50.0, errors=("late_primary",)) for i in range(3)]
    advice = insights.recommendations(records, state, {"13": "Утечка газа"}, {"late_primary": "П"})
    assert len(advice) == 3
    assert "Утечка газа" in advice[0] and "норматива" in advice[1] and "«П»" in advice[2]
    assert insights.recommendations([], RatingState(), {}, {})[0].startswith("Пройдите")
