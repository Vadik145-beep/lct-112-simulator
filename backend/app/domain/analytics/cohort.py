"""The simulated cohort on disk (``ai/data/cohort.json``): written by
``scripts/simulate_cohort.py``, read by ``train_readiness``."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from app.domain.analytics.features import compute_features
from app.domain.analytics.records import AttemptRecord
from app.domain.analytics.simulate import VirtualStudent

COHORT_FILE = "cohort.json"


def record_to_dict(r: AttemptRecord) -> dict[str, Any]:
    data = asdict(r)
    data["at"] = r.at.isoformat()
    data["errors"] = list(r.errors)
    return data


def record_from_dict(data: dict[str, Any]) -> AttemptRecord:
    return AttemptRecord(
        at=datetime.fromisoformat(data["at"]),
        mode=data["mode"],
        incident_group=data["incident_group"],
        difficulty=int(data["difficulty"]),
        total=float(data["total"]),
        passed=bool(data["passed"]),
        seconds=None if data.get("seconds") is None else float(data["seconds"]),
        norm_seconds=int(data["norm_seconds"]),
        decision_correct=data.get("decision_correct"),
        topics_missed=int(data.get("topics_missed") or 0),
        errors=tuple(data.get("errors") or ()),
        session_id=data.get("session_id"),
        attempt_id=data.get("attempt_id"),
    )


def save_cohort(students: list[VirtualStudent], folder: Path, *, seed: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "seed": seed,
        "students": [
            {
                "level": round(s.trainee.level, 4),
                "history": [record_to_dict(r) for r in s.history],
                "certification": [record_to_dict(r) for r in s.certification],
                "ready": s.ready,
            }
            for s in students
        ],
    }
    path = folder / COHORT_FILE
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def load_cohort(folder: Path) -> list[dict[str, Any]]:
    payload = json.loads((folder / COHORT_FILE).read_text(encoding="utf-8"))
    return payload["students"]


def dataset(students: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray]:
    """Feature matrix and labels of a loaded cohort."""
    rows = []
    labels = []
    for s in students:
        history = [record_from_dict(r) for r in s["history"]]
        rows.append(compute_features(history).vector())
        labels.append(1.0 if s["ready"] else 0.0)
    return np.asarray(rows, dtype=float), np.asarray(labels, dtype=float)
