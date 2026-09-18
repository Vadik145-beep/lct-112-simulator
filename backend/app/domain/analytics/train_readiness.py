"""Trains the readiness forecast on the simulated cohort and stores the model with its
metrics (PRD 9.7).

Run from backend/: python -m app.domain.analytics.train_readiness
Needs ai/data/cohort.json from scripts/simulate_cohort.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.config import get_settings
from app.domain.analytics import cohort as cohort_io
from app.domain.analytics import model as readiness

MIN_AUC = 0.75


def main(analytics_dir: Path | None = None) -> dict:
    root = analytics_dir or Path(get_settings().analytics_dir)
    students = cohort_io.load_cohort(root / "data")
    x, y = cohort_io.dataset(students)
    model, metrics = readiness.train(x, y)
    readiness.save(model, metrics, root / "models")
    print(
        f"обучающихся: {metrics['n_total']} (обучение {metrics['n_train']}, "
        f"проверка {metrics['n_test']}), доля готовых в проверке: "
        f"{metrics['positive_share_test']:.2f}"
    )
    print(
        f"ROC AUC на отложенной выборке: {metrics['roc_auc']:.3f}, "
        f"Brier: {metrics['brier']:.4f} (без модели {metrics['brier_baseline']:.4f})"
    )
    print("калибровка (прогноз -> факт, число):")
    for bucket in metrics["calibration"]:
        if bucket["count"]:
            print(
                f"  {bucket['from']:.1f}–{bucket['to']:.1f}: "
                f"{bucket['predicted']:.2f} -> {bucket['observed']:.2f} ({bucket['count']})"
            )
    print(f"сохранено в {root / 'models'}")
    return metrics


if __name__ == "__main__":
    result = main()
    if result["roc_auc"] < MIN_AUC:
        print(f"AUC ниже {MIN_AUC}: признаки надо дорабатывать", file=sys.stderr)
        sys.exit(1)
