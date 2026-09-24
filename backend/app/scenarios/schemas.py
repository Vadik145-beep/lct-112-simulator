"""Request and response models of the scenario API (PRD 11, teacher)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["call_intake", "card_response"]
ScenarioStatus = Literal["draft", "review", "approved", "archived"]

# ---------------------------------------------------------------- reference for the forms


class PersonaOut(BaseModel):
    code: str
    title: str
    voice: str
    style: str


class NoiseOut(BaseModel):
    code: str
    title: str


class TopicOut(BaseModel):
    code: str
    title: str


class ScenarioOptionsOut(BaseModel):
    """Everything the generation form and the card need to name things."""

    personas: list[PersonaOut]
    noises: list[NoiseOut]
    topics: list[TopicOut]
    voices: list[NoiseOut]  # code + description
    sources: list[NoiseOut]
    statuses: list[NoiseOut]
    generation_method: str  # llm | template
    generation_available: bool
    tts_available: bool
    grammar_available: bool


# ---------------------------------------------------------------- list and card


class ScenarioListItem(BaseModel):
    delivered: bool = False
    id: uuid.UUID
    kind: Kind
    title: str
    ticket_ref: str | None
    incident_type_code: str | None
    incident_type_title: str | None
    incident_group_code: str | None
    incident_group_title: str | None
    service_code: str | None
    difficulty: int
    status: ScenarioStatus
    source: str
    current_version: int
    replies_total: int
    replies_approved: int
    replies_pending: int  # generated in a call (hybrid) and waiting for the teacher
    reference_approved: bool
    created_at: datetime
    updated_at: datetime


class ScenarioListOut(BaseModel):
    items: list[ScenarioListItem]
    total: int


class VersionOut(BaseModel):
    version: int
    created_at: datetime
    created_by: str | None
    revision_comment: str | None
    is_current: bool


class ReplyOut(BaseModel):
    id: int
    topic: str
    topic_title: str
    text: str
    approved: bool
    audio_url: str | None
    source: str | None = None  # generated (hybrid, pending) | None
    voicing: Literal["none", "queued", "done", "failed"] = "none"


class ScenarioOut(BaseModel):
    id: uuid.UUID
    # Delivered with the product (data/seed/scenarios): shown, played, but never edited here.
    delivered: bool = False
    kind: Kind
    title: str
    ticket_ref: str | None
    ticket_situation: str | None
    ticket_address: str | None
    incident_type_code: str | None
    incident_type_title: str | None
    incident_group_title: str | None
    service_code: str | None
    difficulty: int
    status: ScenarioStatus
    source: str
    current_version: int
    author: str | None
    created_at: datetime
    body: dict[str, Any]
    replies: list[ReplyOut]
    reference_approved: bool
    fully_approved: bool
    problems: list[str]  # reference checks; approval is refused while non-empty
    # Remarks on the wording and the card: shown to the teacher, never block anything.
    quality: list[str]
    services: list[NoiseOut]  # expected services with titles
    versions: list[VersionOut]
    generation: dict[str, Any] | None


class ScenarioCreateIn(BaseModel):
    body: dict[str, Any]
    status: Literal["draft", "review"] = "draft"


class ScenarioUpdateIn(BaseModel):
    body: dict[str, Any]


class GenerateIn(BaseModel):
    kind: Kind
    phrase: str = Field(min_length=3, max_length=500)
    incident_type: str | None = Field(default=None, max_length=24)
    incident_group: str | None = Field(default=None, max_length=8)
    difficulty: int | None = Field(default=None, ge=1, le=3)
    persona: str | None = Field(default=None, max_length=32)
    noise: str | None = Field(default=None, max_length=16)
    both_kinds: bool = False  # also the other mode from the same phrase


class ReviseIn(BaseModel):
    comment: str = Field(min_length=3, max_length=1000)


class ScenarioRemoveOut(BaseModel):
    result: Literal["deleted", "archived"]


class ApproveIn(BaseModel):
    reference: bool = True
    replies: bool = True  # all replies (call_intake)
    confirm_grammar: bool = False  # approve although grammar errors were reported


class ReplyIn(BaseModel):
    topic: str | None = Field(default=None, max_length=40)
    text: str | None = Field(default=None, min_length=1, max_length=400)


class ReplyCreateIn(BaseModel):
    topic: str = Field(max_length=40)
    text: str = Field(min_length=1, max_length=400)


class RepliesApproveIn(BaseModel):
    reply_ids: list[int] | None = None  # None = all
    confirm_grammar: bool = False


class GrammarIssueOut(BaseModel):
    field: str  # description | reply:<id> | comment:<n>
    text: str
    offset: int
    length: int
    message: str
    replacements: list[str]


class GrammarReportOut(BaseModel):
    available: bool
    method: str
    issues: list[GrammarIssueOut]


class PreviewTurnIn(BaseModel):
    role: Literal["operator", "caller"]
    text: str = Field(max_length=1000)


class PreviewIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    history: list[PreviewTurnIn] = Field(default_factory=list, max_length=40)
    mode: Literal["select", "hybrid", "generate", "buttons"] | None = None


class PreviewOut(BaseModel):
    reply: str
    topics: list[str]
    operator_topics: list[str]
    reply_id: int | None
    method: str
    audio_url: str | None
    latency_ms: int


class JobOut(BaseModel):
    id: str
    type: str
    status: Literal["queued", "running", "done", "failed"]
    progress: int
    message: str | None
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobAcceptedOut(BaseModel):
    job_id: str


class ReferenceDocOut(BaseModel):
    name: str
    chunks: int
    size: int
    uploaded_at: datetime
