"""Readiness forecast model (PRD 9.7): logistic regression on standardized features with
Platt calibration, plus the metrics that say how far it can be trusted (ROC AUC, Brier score,
calibration buckets). Only numpy: the model is a few dozen numbers in a JSON file.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import numpy as np

from app.domain.analytics.features import FEATURE_NAMES, Features, explain

MODEL_FILE = "readiness_model.json"
METRICS_FILE = "readiness_metrics.json"
CALIBRATION_BINS = 10
# Risk colours of the teacher's table (probability of passing the certification).
RISK_HIGH_BELOW = 0.4
RISK_LOW_FROM = 0.7


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -30, 30)))


@dataclass
class ReadinessModel:
    feature_names: list[str]
    mean: list[float]
    std: list[float]
    weights: list[float]
    bias: float
    # Platt scaling p = sigmoid(a * z + b) applied to the raw logit z.
    platt_a: float = 1.0
    platt_b: float = 0.0
    trained_at: str = ""
    n_train: int = 0

    # ----------------------------------------------------------------- inference

    def _standardize(self, x: np.ndarray) -> np.ndarray:
        return (x - np.asarray(self.mean)) / np.asarray(self.std)

    def logits(self, x: np.ndarray) -> np.ndarray:
        z = self._standardize(np.atleast_2d(x))
        return z @ np.asarray(self.weights) + self.bias

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        return _sigmoid(self.platt_a * self.logits(x) + self.platt_b)

    def predict_one(self, features: Features) -> Prediction:
        x = np.asarray([features.vector()], dtype=float)
        p = float(self.predict_proba(x)[0])
        z = self._standardize(x)[0]
        contributions = z * np.asarray(self.weights) * self.platt_a
        # The two features that pushed the forecast the most in its own direction.
        ready = p >= 0.5
        order = np.argsort(contributions if not ready else -contributions)
        reasons: list[str] = []
        for i in order:
            c = float(contributions[i])
            if (ready and c <= 0) or (not ready and c >= 0):
                continue
            name = self.feature_names[i]
            reasons.append(explain(name, features.values[name], favourable=ready))
            if len(reasons) == 2:
                break
        if not reasons:
            for i in np.argsort(-np.abs(contributions))[:2]:
                name = self.feature_names[int(i)]
                reasons.append(
                    explain(name, features.values[name], favourable=float(contributions[i]) > 0)
                )
        return Prediction(probability=round(p, 3), risk=risk_level(p), reasons=reasons)

    # ----------------------------------------------------------------- persistence

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadinessModel:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def load(cls, folder: Path) -> ReadinessModel | None:
        path = folder / MODEL_FILE
        if not path.exists():
            return None
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))


@dataclass
class Prediction:
    probability: float
    risk: str
    reasons: list[str] = field(default_factory=list)


def risk_level(probability: float) -> str:
    if probability < RISK_HIGH_BELOW:
        return "high"
    if probability < RISK_LOW_FROM:
        return "medium"
    return "low"


# --------------------------------------------------------------------- training


def _fit_logistic(
    x: np.ndarray, y: np.ndarray, *, l2: float = 1.0, iterations: int = 3000, lr: float = 0.1
) -> tuple[np.ndarray, float]:
    """Gradient descent on the L2-regularized log loss (features already standardized)."""
    n, d = x.shape
    w = np.zeros(d)
    b = 0.0
    for _ in range(iterations):
        p = _sigmoid(x @ w + b)
        grad_w = x.T @ (p - y) / n + l2 * w / n
        grad_b = float(np.mean(p - y))
        w -= lr * grad_w
        b -= lr * grad_b
    return w, b


def _fit_platt(
    z: np.ndarray, y: np.ndarray, iterations: int = 3000, lr: float = 0.05
) -> tuple[float, float]:
    """One-dimensional logistic fit of the labels on the raw logits."""
    a, b = 1.0, 0.0
    scale = float(np.std(z)) or 1.0
    zs = z / scale
    for _ in range(iterations):
        p = _sigmoid(a * zs + b)
        a -= lr * float(np.mean((p - y) * zs))
        b -= lr * float(np.mean(p - y))
    return a / scale, b


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Mann–Whitney form of the ROC AUC (ties count half)."""
    pos = p[y == 1]
    neg = p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    greater = (pos[:, None] > neg[None, :]).sum()
    equal = (pos[:, None] == neg[None, :]).sum()
    return float((greater + 0.5 * equal) / (len(pos) * len(neg)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def calibration(y: np.ndarray, p: np.ndarray, bins: int = CALIBRATION_BINS) -> list[dict]:
    """Per bucket of predicted probability: mean forecast, observed share, count."""
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in pairwise(edges):
        mask = (p >= lo) & (p < hi if hi < 1 else p <= hi)
        n = int(mask.sum())
        out.append(
            {
                "from": round(float(lo), 2),
                "to": round(float(hi), 2),
                "predicted": round(float(p[mask].mean()), 3) if n else None,
                "observed": round(float(y[mask].mean()), 3) if n else None,
                "count": n,
            }
        )
    return out


def train(
    x: np.ndarray, y: np.ndarray, *, seed: int = 7, test_share: float = 0.2
) -> tuple[ReadinessModel, dict[str, Any]]:
    """80/20 split by trainee; inside the training part another quarter is held out to fit
    the Platt calibration so the calibration never sees the test rows."""
    rng = np.random.default_rng(seed)
    n = len(y)
    order = rng.permutation(n)
    n_test = round(n * test_share)
    test_idx, train_idx = order[:n_test], order[n_test:]
    n_cal = round(len(train_idx) * 0.25)
    cal_idx, fit_idx = train_idx[:n_cal], train_idx[n_cal:]

    mean = x[fit_idx].mean(axis=0)
    std = x[fit_idx].std(axis=0)
    std[std == 0] = 1.0
    zs = (x - mean) / std
    w, b = _fit_logistic(zs[fit_idx], y[fit_idx])
    raw = zs @ w + b
    a, pb = _fit_platt(raw[cal_idx], y[cal_idx])

    model = ReadinessModel(
        feature_names=list(FEATURE_NAMES),
        mean=[float(v) for v in mean],
        std=[float(v) for v in std],
        weights=[float(v) for v in w],
        bias=float(b),
        platt_a=float(a),
        platt_b=float(pb),
        trained_at=datetime.now(UTC).isoformat(timespec="seconds"),
        n_train=len(train_idx),
    )
    p_test = model.predict_proba(x[test_idx])
    y_test = y[test_idx]
    metrics = {
        "trained_at": model.trained_at,
        "n_total": int(n),
        "n_train": len(train_idx),
        "n_test": int(n_test),
        "positive_share_test": round(float(y_test.mean()), 3),
        "roc_auc": round(roc_auc(y_test, p_test), 3),
        "brier": round(brier(y_test, p_test), 4),
        "brier_baseline": round(brier(y_test, np.full_like(p_test, y_test.mean())), 4),
        "calibration": calibration(y_test, p_test),
        "features": [
            {"name": name, "weight": round(float(wi), 3)}
            for name, wi in zip(FEATURE_NAMES, w, strict=True)
        ],
    }
    return model, metrics


def save(model: ReadinessModel, metrics: dict[str, Any], folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MODEL_FILE).write_text(
        json.dumps(model.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (folder / METRICS_FILE).write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_metrics(folder: Path) -> dict[str, Any] | None:
    path = folder / METRICS_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
