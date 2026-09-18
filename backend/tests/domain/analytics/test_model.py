"""Readiness forecast (PRD 9.7): features, the simulated cohort, training and metrics."""

from datetime import UTC, datetime, timedelta

import numpy as np

from app.domain.analytics import model as readiness
from app.domain.analytics.features import FEATURE_NAMES, compute_features
from app.domain.analytics.records import AttemptRecord, from_result
from app.domain.analytics.simulate import simulate_cohort


def record(i: int, total: float, **kw) -> AttemptRecord:
    base = {
        "at": datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=i),
        "mode": "card_response",
        "incident_group": "1",
        "difficulty": 2,
        "total": total,
        "passed": total >= 70,
        "seconds": 25.0,
        "norm_seconds": 30,
        "decision_correct": True,
        "topics_missed": 0,
        "errors": (),
    }
    return AttemptRecord(**{**base, **kw})


def test_features_follow_the_recent_window() -> None:
    history = [record(i, 50) for i in range(10)] + [record(10 + i, 90) for i in range(10)]
    f = compute_features(history)
    assert f.values["mean_recent"] == 90
    assert f.values["attempts"] == 20
    assert f.values["trend"] == 0
    rising = compute_features([record(i, 50 + 5 * i) for i in range(10)])
    assert rising.values["trend"] > 0
    assert list(f.values) == list(FEATURE_NAMES)


def test_features_count_decisions_topics_and_errors() -> None:
    history = [
        record(0, 60, decision_correct=False, errors=("late_primary",)),
        record(1, 80, decision_correct=True),
        record(2, 40, mode="call_intake", decision_correct=None, topics_missed=2),
    ]
    f = compute_features(history)
    assert f.values["wrong_decisions"] == 0.5
    assert f.values["topics_missed"] == 2
    assert round(f.values["errors_per_attempt"], 3) == round(1 / 3, 3)


def test_from_result_reads_a_stored_evaluation() -> None:
    result = {
        "total": 64,
        "passed": False,
        "components": {
            "time": {"items": [{"seconds": 41.5, "norm_seconds": 30}]},
            "decision": {"items": [{"expected": "accept", "actual": "reject"}]},
        },
        "errors": [{"code": "late_primary"}, {"code": "status_mismatch"}],
    }
    at = datetime(2026, 9, 1, tzinfo=UTC)
    r = from_result(
        at=at,
        mode="card_response",
        incident_type_code="13.2.4.0",
        difficulty=2,
        norm_seconds=30,
        result=result,
    )
    assert r is not None
    assert r.incident_group == "13"
    assert r.seconds == 41.5 and r.decision_correct is False
    assert r.errors == ("late_primary", "status_mismatch")
    empty = from_result(
        at=at,
        mode="card_response",
        incident_type_code=None,
        difficulty=1,
        norm_seconds=30,
        result=None,
    )
    assert empty is None


def test_simulated_cohort_is_reproducible_and_balanced() -> None:
    a = simulate_cohort(40, seed=1)
    b = simulate_cohort(40, seed=1)
    assert [s.ready for s in a] == [s.ready for s in b]
    assert all(len(s.certification) == 20 for s in a)
    assert 0.2 < sum(s.ready for s in a) / len(a) < 0.8


def test_training_reaches_the_required_auc_and_explains_forecasts() -> None:
    students = simulate_cohort(300, seed=3)
    x = np.asarray([compute_features(s.history).vector() for s in students])
    y = np.asarray([1.0 if s.ready else 0.0 for s in students])
    model, metrics = readiness.train(x, y)
    assert metrics["roc_auc"] >= 0.75
    assert metrics["brier"] < metrics["brier_baseline"]
    assert sum(b["count"] for b in metrics["calibration"]) == metrics["n_test"]

    weak = model.predict_one(compute_features([record(i, 35, seconds=60.0) for i in range(12)]))
    strong = model.predict_one(compute_features([record(i, 95) for i in range(30)]))
    assert weak.probability < 0.5 < strong.probability
    assert weak.risk == "high" and strong.risk == "low"
    assert len(weak.reasons) == 2 and len(strong.reasons) == 2


def test_model_round_trips_through_json(tmp_path) -> None:
    students = simulate_cohort(60, seed=5)
    x = np.asarray([compute_features(s.history).vector() for s in students])
    y = np.asarray([1.0 if s.ready else 0.0 for s in students])
    model, metrics = readiness.train(x, y)
    readiness.save(model, metrics, tmp_path)
    loaded = readiness.ReadinessModel.load(tmp_path)
    assert loaded is not None
    assert np.allclose(loaded.predict_proba(x), model.predict_proba(x))
    assert readiness.load_metrics(tmp_path) == metrics
    assert readiness.ReadinessModel.load(tmp_path / "missing") is None


def test_metrics_helpers() -> None:
    y = np.array([0, 0, 1, 1])
    assert readiness.roc_auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert readiness.roc_auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert readiness.brier(y, y.astype(float)) == 0.0
    assert readiness.risk_level(0.2) == "high"
    assert readiness.risk_level(0.5) == "medium"
    assert readiness.risk_level(0.9) == "low"
