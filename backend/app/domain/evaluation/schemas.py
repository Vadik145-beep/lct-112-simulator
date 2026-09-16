"""Scenario bodies (PRD 9.2, 9.3) and attempt inputs the evaluation engines consume.

Scenario bodies are stored as ``scenario_versions.body`` and validated here; attempt inputs are
what the attempt endpoints accumulate (``attempts.status_log``, ``attempts.dialog``, the
submitted card). Everything is plain data: no I/O, no database.

Scenario models ignore unknown keys (bodies carry editorial fields such as ``status`` or
``note`` that the engines do not need); attempt models forbid them, so a typo in the API
payload is caught at once.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MOSCOW = "Москва"
SCENARIO = ConfigDict(extra="ignore")
STRICT = ConfigDict(extra="forbid")


class Address(BaseModel):
    model_config = SCENARIO

    region: str | None = None  # None means Moscow; anything else is another region
    city: str | None = None
    street: str | None = None
    house: str | None = None
    building: str | None = None  # корпус
    structure: str | None = None  # строение
    entrance: str | None = None
    floor: str | None = None
    code: str | None = None  # домофон
    apartment: str | None = None
    okrug: str | None = None
    district: str | None = None
    descriptive: str | None = None  # описательный адрес, когда точного нет

    def is_moscow(self) -> bool:
        from app.domain.evaluation.text import normalize_text

        region = normalize_text(self.region or "")
        return region in {"", "москва", "г москва", "город москва"}


class Caller(BaseModel):
    model_config = SCENARIO

    name: str | None = None
    role: str | None = None  # очевидец, пострадавший, родственник…
    phone: str | None = None


# --- card_response ---------------------------------------------------------------------------


class NotifiedService(BaseModel):
    model_config = SCENARIO

    service: str
    status: str  # title from the memo, e.g. «Получена службой»


class Card(BaseModel):
    """The incident card as the dispatcher sees it in the journal."""

    model_config = SCENARIO

    number: str | None = None  # journal number; the interface assigns one when missing
    incident_type: str
    signs: list[str] = Field(default_factory=list)
    flags: dict[str, bool] = Field(default_factory=dict)
    address: Address = Field(default_factory=Address)
    caller: Caller = Field(default_factory=Caller)
    description: str = ""
    notified: list[NotifiedService] = Field(default_factory=list)


class ReferenceStep(BaseModel):
    model_config = SCENARIO

    status: str  # response status code (app.domain.reference_data.RESPONSE_STATUSES)
    order_number: bool = False  # the step must carry a squad number
    comment_example: str | None = None  # a comment is expected; the text is a reference


class CardResponseReference(BaseModel):
    model_config = SCENARIO

    decision: Literal["accept", "reject"]
    reject_reason: str | None = None  # code from REJECT_REASONS when decision is reject
    status_chain: list[ReferenceStep]
    critical_errors: list[str] = Field(default_factory=list)


class CardResponseScenario(BaseModel):
    model_config = SCENARIO

    kind: Literal["card_response"]
    title: str
    ticket_ref: str | None = None
    service: str  # service the student acts for
    difficulty: int = 1
    norm_seconds: int = 30
    duplicate_of: str | None = None  # number of the earlier card (duplicate scenarios)
    card: Card
    reference: CardResponseReference


class StatusEntry(BaseModel):
    """One row of ``attempts.status_log``."""

    model_config = STRICT

    status: str
    at: datetime
    order_number: str | None = None
    comment: str | None = None
    reject_reason: str | None = None  # code chosen in the drop-down for «Не принята» / «Отказ»


class CardResponseAttempt(BaseModel):
    model_config = STRICT

    issued_at: datetime  # «Добавлена»: the 30 seconds count from here
    received_at: datetime | None = None  # «Получена службой»
    status_log: list[StatusEntry] = Field(default_factory=list)


# --- call_intake -----------------------------------------------------------------------------


class CallerProfile(BaseModel):
    model_config = SCENARIO

    persona: str = "calm"
    voice: str | None = None
    noise: str | None = None
    opening: str = ""
    facts: dict[str, str] = Field(default_factory=dict)
    behaviour: str | None = None
    drops_call: bool = False  # the caller hangs up before the operator finishes
    no_contact: bool = False  # nobody answers the call back


class Reply(BaseModel):
    model_config = SCENARIO

    id: int
    topic: str
    text: str
    audio: str | None = None
    approved: bool = False


class ReferenceCard(BaseModel):
    model_config = SCENARIO

    signs_path: list[str] = Field(default_factory=list)
    incident_type: str
    flags: dict[str, bool] = Field(default_factory=dict)
    address: Address = Field(default_factory=Address)
    caller: Caller = Field(default_factory=Caller)
    description_keywords: list[str] = Field(default_factory=list)
    description: str | None = None
    expected_services: list[str] = Field(default_factory=list)  # resolve_services(reference)


class CallIntakeScenario(BaseModel):
    model_config = SCENARIO

    kind: Literal["call_intake"]
    title: str
    ticket_ref: str | None = None
    difficulty: int = 1
    norm_seconds: int = 90
    caller: CallerProfile
    replies: list[Reply] = Field(default_factory=list)
    required_topics: list[str]
    reference_card: ReferenceCard


class DialogTurn(BaseModel):
    """One row of ``attempts.dialog``; ``topics`` come from the dialog engine when it knows
    them, otherwise the evaluation infers them from the text by keywords."""

    model_config = STRICT

    role: Literal["operator", "caller"]
    text: str
    topics: list[str] = Field(default_factory=list)
    at: datetime | None = None


class SubmittedCard(BaseModel):
    model_config = STRICT

    signs_path: list[str] = Field(default_factory=list)
    incident_type: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)
    services: list[str] = Field(default_factory=list)  # filled by resolve_services in the UI
    address: Address = Field(default_factory=Address)
    caller: Caller = Field(default_factory=Caller)
    description: str = ""


class CallIntakeAttempt(BaseModel):
    model_config = STRICT

    answered_at: datetime
    submitted_at: datetime | None = None
    card: SubmittedCard = Field(default_factory=SubmittedCard)
    dialog: list[DialogTurn] = Field(default_factory=list)
    call_dropped_marked: bool = False
    no_contact_marked: bool = False


Scenario = CardResponseScenario | CallIntakeScenario


def parse_scenario(body: dict) -> Scenario:
    kind = body.get("kind")
    if kind == "card_response":
        return CardResponseScenario.model_validate(body)
    if kind == "call_intake":
        return CallIntakeScenario.model_validate(body)
    raise ValueError(f"неизвестный вид сценария: {kind!r}")
