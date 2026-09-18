from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class PeriodOut(BaseModel):
    since: datetime
    until: datetime
    days: int


class VolumeOut(BaseModel):
    students: int
    students_with_attempts: int
    attempts: int
    sessions: int


class PersonOut(BaseModel):
    id: uuid.UUID
    login: str
    full_name: str


class IncidentGroupOut(BaseModel):
    code: str
    title: str


class HeatCellOut(BaseModel):
    student_id: uuid.UUID
    incident_group: str
    mode: str
    mean: float
    count: int


class WeekPointOut(BaseModel):
    week_start: datetime
    mode: str
    mean_score: float
    mean_time_ratio: float
    count: int


class ErrorCountOut(BaseModel):
    code: str
    title: str
    count: int
    students: int
    students_share: float


class ReadinessOut(BaseModel):
    student_id: uuid.UUID
    full_name: str
    attempts: int
    probability: float | None
    # low | medium | high | unknown (no attempts or no model)
    risk: str
    reasons: list[str]


class GroupAnalyticsOut(BaseModel):
    group_id: uuid.UUID
    group_title: str
    period: PeriodOut
    demo_data: bool
    volume: VolumeOut
    students: list[PersonOut]
    incident_groups: list[IncidentGroupOut]
    heatmap: list[HeatCellOut]
    dynamics: list[WeekPointOut]
    errors: list[ErrorCountOut]
    readiness: list[ReadinessOut]
    model_available: bool
    summary: str


class CalibrationBucketOut(BaseModel):
    lower: float
    upper: float
    predicted: float | None
    observed: float | None
    count: int


class FeatureWeightOut(BaseModel):
    name: str
    title: str
    weight: float


class ReadinessModelOut(BaseModel):
    trained: bool
    trained_at: datetime | None = None
    n_total: int = 0
    n_train: int = 0
    n_test: int = 0
    positive_share_test: float | None = None
    roc_auc: float | None = None
    brier: float | None = None
    brier_baseline: float | None = None
    calibration: list[CalibrationBucketOut] = []
    features: list[FeatureWeightOut] = []


class RatingOut(BaseModel):
    incident_group: str
    title: str
    mode: str
    rating: float
    n: int
