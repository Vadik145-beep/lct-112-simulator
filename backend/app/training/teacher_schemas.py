"""Request and response models of the teacher API: groups, sessions, monitoring, report."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models import MODE_CARD_RESPONSE

# ---------------------------------------------------------------- groups


class StudentOut(BaseModel):
    id: uuid.UUID
    login: str
    full_name: str
    service_code: str | None


class GroupOut(BaseModel):
    id: uuid.UUID
    title: str
    members: list[StudentOut]
    created_at: datetime


class GroupIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    student_ids: list[uuid.UUID] = Field(default_factory=list)


class GroupPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    student_ids: list[uuid.UUID] | None = None


# ---------------------------------------------------------------- sessions


class SessionIn(BaseModel):
    """Settings of a lesson (PRD 13.7). Fields not sent keep the trainer defaults."""

    title: str = Field(min_length=1, max_length=300)
    mode: str = MODE_CARD_RESPONSE
    group_id: uuid.UUID
    card_source: str = "scenarios"
    # Explicit queue; empty = pick approved scenarios by the filters below at start.
    scenario_ids: list[uuid.UUID] = Field(default_factory=list)
    incident_groups: list[str] = Field(default_factory=list)
    difficulty: int = 1
    service_profile: list[str] = Field(default_factory=list)
    norm_seconds: int = 30
    pass_threshold: int = 70
    hints_enabled: bool = True
    cards_per_student: int = 0
    # Empty = the default of the administrator's settings (48 hours out of the box).
    unfinished_seconds: int | None = None
    weights: dict[str, int] = Field(default_factory=dict)
    # Call-intake settings (PRD 9.3): the operator speaks through the softphone, and how the
    # caller answers (select | hybrid | generate | buttons | live). Kept on card sessions too.
    voice_enabled: bool = False
    dialog_mode: str = "select"
    # Adaptive selection (PRD 9.7): each trainee's next card comes from the weakest incident
    # group at a difficulty near the skill rating, unseen scenarios first.
    adaptive: bool = False


class SessionPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    mode: str | None = None
    group_id: uuid.UUID | None = None
    card_source: str | None = None
    scenario_ids: list[uuid.UUID] | None = None
    incident_groups: list[str] | None = None
    difficulty: int | None = None
    service_profile: list[str] | None = None
    norm_seconds: int | None = None
    pass_threshold: int | None = None
    hints_enabled: bool | None = None
    cards_per_student: int | None = None
    unfinished_seconds: int | None = None
    weights: dict[str, int] | None = None
    voice_enabled: bool | None = None
    dialog_mode: str | None = None
    adaptive: bool | None = None


class QueueScenarioOut(BaseModel):
    id: uuid.UUID
    title: str
    difficulty: int
    service_code: str | None
    incident_type_code: str | None


class SessionListItem(BaseModel):
    id: uuid.UUID
    title: str
    mode: str
    status: str
    group_id: uuid.UUID | None
    group_title: str
    difficulty: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    students: int
    evaluated: int
    average: float | None


class SessionOut(SessionListItem):
    card_source: str
    scenario_ids: list[uuid.UUID]
    incident_groups: list[str]
    service_profile: list[str]
    norm_seconds: int
    pass_threshold: int
    hints_enabled: bool
    cards_per_student: int
    unfinished_seconds: int
    weights: dict[str, int]
    voice_enabled: bool
    dialog_mode: str
    adaptive: bool
    members: list[StudentOut]
    # The queue: fixed once the session starts, a preview of the current filters before.
    queue: list[QueueScenarioOut]
    last_seq: int


# ---------------------------------------------------------------- monitoring


class MonitorCard(BaseModel):
    attempt_id: uuid.UUID
    card_number: str
    incident_title: str
    state: str
    response_status: str
    response_status_title: str
    card_status: str
    issued_at: datetime
    received_at: datetime | None
    primary_status_at: datetime | None
    submitted_at: datetime | None
    total: float | None
    passed: bool | None


class MonitorStudent(BaseModel):
    student_id: uuid.UUID
    full_name: str
    login: str
    service_code: str | None
    active: list[MonitorCard]
    finished: int
    passed: int
    average: float | None
    last_total: float | None
    last_attempt_id: uuid.UUID | None


class MonitorOut(BaseModel):
    session_id: uuid.UUID
    status: str
    norm_seconds: int
    pass_threshold: int
    cards_total: int
    students: list[MonitorStudent]
    last_seq: int


class ProgressRequest(BaseModel):
    """What the trainee is doing in the card right now (shown on the monitoring tile)."""

    stage: str = Field(min_length=1, max_length=32)


# ---------------------------------------------------------------- report


class ReportErrorCount(BaseModel):
    code: str
    title: str
    count: int


class ReportAttempt(BaseModel):
    id: uuid.UUID
    card_number: str
    scenario_title: str
    incident_title: str
    state: str
    card_status: str
    card_status_title: str
    issued_at: datetime
    submitted_at: datetime | None
    total: float | None
    passed: bool | None
    # Seconds from «Добавлена» to the primary status and the difference from the norm.
    seconds: float | None
    norm_seconds: int
    deviation: float | None
    decision_expected: str | None
    decision_actual: str | None
    decision_correct: bool | None
    errors: list[str]
    grammar_percent: float | None
    # Remarks of the review (PRD 13.7: «информация о действиях, замечаниях…»): every error
    # with its explanation, the teacher's override and comments.
    remarks: list[str] = []
    overridden: bool = False
    override_reason: str | None = None
    comments: list[str] = []


class ReportStudent(BaseModel):
    student_id: uuid.UUID
    full_name: str
    login: str
    service_code: str | None
    attempts: list[ReportAttempt]
    attempts_total: int
    evaluated: int
    passed: int
    average: float | None
    average_seconds: float | None
    average_deviation: float | None
    wrong_decisions: int
    typical_errors: list[ReportErrorCount]
    grammar_percent: float | None


class ReportSummary(BaseModel):
    students: int
    participated: int
    evaluated: int
    passed: int
    average: float | None
    average_seconds: float | None
    typical_errors: list[ReportErrorCount]


class ReportOut(BaseModel):
    session_id: uuid.UUID
    title: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    norm_seconds: int
    pass_threshold: int
    summary: ReportSummary
    students: list[ReportStudent]


class ExportRequest(BaseModel):
    session_id: uuid.UUID
    format: str
    url: str
