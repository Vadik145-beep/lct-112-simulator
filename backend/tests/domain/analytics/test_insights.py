"""Group insights: heat map cells, weekly dynamics, top errors, template texts."""

from datetime import UTC, datetime, timedelta

from app.domain.analytics import insights
from app.domain.analytics.features import compute_features
from app.domain.analytics.rating import RatingState
from app.domain.analytics.records import MAX_TIME_RATIO, NO_TIME_RATIO, AttemptRecord

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
        weakest_group=("Утечка газа", "call_intake", 51.0),
        errors=insights.top_errors({"a": [record(0, 50, errors=("x",))]}, {"x": "Ошибка X"}),
        at_risk=2,
    )
    assert "12 оценённых попыток" in text and "Ошибка X" in text
    # The weakest cell is named with its mode, so the number matches the heat map.
    assert "«Утечка газа» в приёме вызова" in text
    assert "риском" in text


def test_summary_declines_numbers() -> None:
    """The summary is read by a teacher, not by a log: «у 1 обучающегося», not «у 1
    обучающихся»."""
    one = insights.group_summary(
        students=1,
        attempts=1,
        mean_score=45.0,
        weakest_group=None,
        errors=insights.top_errors({"a": [record(0, 50, errors=("x",))]}, {"x": "Ошибка X"}),
        at_risk=0,
    )
    assert "1 оценённая попытка у 1 обучающегося" in one
    assert "1 раз у 1 из 1 обучающегося" in one
    two = insights.group_summary(
        students=2, attempts=2, mean_score=45.0, weakest_group=None, errors=[], at_risk=0
    )
    assert "2 оценённые попытки у 2 обучающихся" in two

    state = RatingState()
    state.apply("13", "card_response", 2, 10)
    records = [record(i, 40, seconds=50.0, errors=("late_primary",)) for i in range(3)]
    advice = insights.recommendations(records, state, {"13": "Утечка газа"}, {"late_primary": "П"})
    assert len(advice) == 3
    assert "Утечка газа" in advice[0] and "норматива" in advice[1] and "«П»" in advice[2]
    assert insights.recommendations([], RatingState(), {}, {})[0].startswith("Пройдите")


def test_abandoned_attempt_does_not_move_the_week() -> None:
    """An attempt left open overnight counts as MAX_TIME_RATIO norms, not as 2500 of them:
    otherwise one card drags the whole «Время, % от норматива» line of the group."""
    normal = [record(0, 60, seconds=30.0), record(0, 60, seconds=45.0)]
    abandoned = record(0, 21, seconds=75_547.0, norm_seconds=90)
    assert abandoned.time_ratio == MAX_TIME_RATIO

    week = insights.dynamics([*normal, abandoned], since=SINCE)[0]
    assert week.count == 3
    # (1.0 + 1.5 + 2.5) / 3 — readable, not 280.
    assert week.mean_time_ratio == 1.67


def test_missing_time_counts_as_twice_the_norm() -> None:
    assert record(0, 40, seconds=None).time_ratio == NO_TIME_RATIO


def test_forecast_features_survive_an_abandoned_attempt() -> None:
    """The time feature of the forecast stays inside the range the model was trained on, so a
    single abandoned attempt cannot pin a trainee at zero probability."""
    records = [record(i, 60, seconds=30.0) for i in range(9)]
    records.append(record(9, 21, seconds=75_547.0, norm_seconds=90))
    assert compute_features(records).values["time_ratio"] <= MAX_TIME_RATIO
