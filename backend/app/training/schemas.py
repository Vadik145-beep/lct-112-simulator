"""Response and request models of the card-response training API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ServiceInfo(BaseModel):
    code: str
    title: str
    short_title: str
    no_reject: bool = False


class SessionInfo(BaseModel):
    id: uuid.UUID
    title: str
    mode: str
    status: str
    difficulty: int
    norm_seconds: int
    hints_enabled: bool
    # Режим диалога занятия: карточке он нужен, чтобы вести служебный звонок через облако.
    dialog_mode: str
    started_at: datetime | None
    finished_at: datetime | None
    service: ServiceInfo | None


class AssignmentOut(SessionInfo):
    active_cards: int
    finished_cards: int


class ArmInfo(BaseModel):
    """Who sits at the emulated workstation (shown in the journal header)."""

    dispatcher: str
    operator_no: str
    arm_no: str


class ServiceStatusOut(BaseModel):
    code: str
    title: str
    short_title: str
    status: str
    status_title: str
    at: datetime | None
    is_own: bool
    is_main: bool = False  # the incident type's main service, underlined on the АРМ-112


class CallerOut(BaseModel):
    name: str = ""
    role: str = ""
    phone: str = ""


class AddressOut(BaseModel):
    text: str
    street: str = ""
    house: str = ""
    building: str = ""
    structure: str = ""
    entrance: str = ""
    floor: str = ""
    apartment: str = ""
    code: str = ""
    okrug: str = ""
    district: str = ""
    descriptive: str = ""


class IncidentOut(BaseModel):
    type_code: str | None
    group_title: str
    final_title: str
    signs: list[str]


class CardOut(BaseModel):
    number: str
    created_at: datetime
    operator_no: str
    arm_no: str
    caller: CallerOut
    address: AddressOut
    description: str
    incident: IncidentOut
    flags: dict[str, bool]
    injured: bool
    injured_count: int | None = None
    ambulance_refused: bool
    blocked: bool
    emergency: bool
    incident_flag: bool
    phones: dict[str, str]
    services: list[ServiceStatusOut]


class JournalItem(BaseModel):
    attempt_id: uuid.UUID
    card_number: str
    state: str
    response_status: str
    response_status_title: str
    card_status: str
    card_status_title: str
    card_status_alert: bool
    issued_at: datetime
    received_at: datetime | None
    primary_status_at: datetime | None
    submitted_at: datetime | None
    norm_seconds: int
    incident_title: str
    incident_group: str
    injured: bool
    address: str
    caller: CallerOut
    description: str
    signs: list[str]
    operator_no: str
    arm_no: str
    services: list[ServiceStatusOut]


class JournalOut(BaseModel):
    session: SessionInfo
    arm: ArmInfo
    items: list[JournalItem]
    page: int
    per_page: int
    total: int
    last_seq: int


class StatusLogEntryOut(BaseModel):
    status: str
    title: str
    order_number: str | None
    comment: str | None
    reject_reason: str | None
    reject_reason_title: str | None
    at: datetime
    by: str


class FlaggedFieldOut(BaseModel):
    """A card field the dispatcher marked as an operator mistake (issue #35)."""

    field: str
    title: str
    corrected_value: str
    at: datetime


class TransitionOut(BaseModel):
    code: str
    title: str
    requires_comment: bool
    requires_order_number: bool
    is_final: bool
    is_primary: bool


class RejectReasonOut(BaseModel):
    code: str
    title: str


class IntakeOut(BaseModel):
    """The call-intake side of an attempt (PRD 13.5): what the operator-112 card needs
    besides the transcript (``GET /attempts/{id}/dialog``)."""

    # Number the softphone shows as АОН: the scenario's phone or a stable stand-in.
    caller_phone: str
    # The card as the trainee is filling it (``PUT /attempts/{id}/draft``) or saved it.
    draft: dict | None
    # Scenario title and the reference card, after the card is saved (PRD 11).
    title: str | None
    required_topics: list[str]


class OverrideOut(BaseModel):
    """The teacher's change of the total (PRD 13.6: the old value struck through)."""

    old_total: float
    old_passed: bool
    new_total: float
    new_passed: bool
    reason: str
    teacher_name: str
    at: datetime


class CommentOut(BaseModel):
    id: uuid.UUID
    text: str
    author_name: str
    at: datetime


class AttemptOut(BaseModel):
    id: uuid.UUID
    session: SessionInfo
    arm: ArmInfo
    state: str
    response_status: str
    response_status_title: str
    card_status: str
    card_status_title: str
    card_status_alert: bool
    issued_at: datetime
    received_at: datetime | None
    primary_status_at: datetime | None
    submitted_at: datetime | None
    norm_seconds: int
    card: CardOut
    service: ServiceInfo | None
    status_log: list[StatusLogEntryOut]
    # Fields flagged as operator mistakes while checking the card (issue #35).
    flagged_fields: list[FlaggedFieldOut] = []
    # Calls to service officers (issue #36), oldest first; the open one has no ended_at.
    service_calls: list[ServiceCallOut] = []
    # Services the reference expects a call to (empty = calls are not evaluated).
    service_calls_required: list[str] = []
    # The squad will report by phone after «Принята» (customer, 21.09.2026): the hints tell
    # the trainee to wait for the reports instead of clicking the statuses through.
    reports_expected: bool = False
    transitions: list[TransitionOut]
    reject_reasons: list[RejectReasonOut]
    # Reference solution, visible after the card is closed (PRD 11: «эталон после завершения»).
    reference: dict | None
    # Filled by the evaluation engine once the card is closed; null until then.
    evaluation: dict | None
    # Present for call-intake attempts only.
    intake: IntakeOut | None = None
    # Teacher's override of the total and comments (wave 9), visible to the trainee too.
    override: OverrideOut | None = None
    comments: list[CommentOut] = []
    last_seq: int


class StatusRequest(BaseModel):
    status: str = Field(min_length=1, max_length=32)
    order_number: str | None = Field(default=None, max_length=64)
    comment: str | None = Field(default=None, max_length=2000)
    reject_reason: str | None = Field(default=None, max_length=32)
    # Client-generated id of the action; a retry with the same id is applied once.
    action_id: str | None = Field(default=None, max_length=64)


class ServiceCallTurnOut(BaseModel):
    index: int
    role: str  # operator = the dispatcher, caller = the officer
    text: str
    topics: list[str] = []
    at: datetime | None = None
    audio_url: str | None = None
    heard: bool = False
    generated: bool = False


class ServiceCallOut(BaseModel):
    """A call on the card: the dispatcher's call to a service officer (issue #36, ``outgoing``),
    the squad leader's report to the dispatcher (``report``, customer 21.09.2026) or the
    dispatcher's call back to the person who reported the incident (``caller``, customer
    23.09.2026)."""

    id: str
    service: str
    service_title: str
    kind: Literal["outgoing", "report", "caller"] = "outgoing"
    report_status: str | None = None
    report_status_title: str | None = None
    started_at: datetime
    answered: bool
    answered_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    telephony: bool
    seconds: float | None
    facts_passed: list[str]
    facts_required: list[str]
    recording_available: bool
    turns: list[ServiceCallTurnOut]


class ServiceCallRequest(BaseModel):
    service: str = Field(min_length=1, max_length=32)


class ServiceCallSayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    action_id: str | None = Field(default=None, max_length=64)


class ServiceCallResponse(BaseModel):
    call: ServiceCallOut
    attempt: AttemptOut
    pending_reply: bool = False
    latency_ms: int = 0
    applied: bool = True
    # The browser microphone can be used (the stt service answers); False = text only.
    stt_available: bool = False
    # What speech recognition understood (utterance only).
    heard_text: str | None = None


class FlagFieldRequest(BaseModel):
    """«Отметить ошибку» in a card field: the path of the field and the value the dispatcher
    considers right (issue #35)."""

    field: str = Field(min_length=1, max_length=64)
    corrected_value: str = Field(min_length=1, max_length=300)
    # Client-generated id of the action; a retry with the same id is applied once.
    action_id: str | None = Field(default=None, max_length=64)


class StatusResponse(BaseModel):
    attempt: AttemptOut
    applied: bool
    # Cards issued right after this one was closed (queue mode).
    issued: list[uuid.UUID]
