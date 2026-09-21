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


SERVICE_CALL_FACTS = ("address", "incident_type", "injured", "order_number", "access")
DEFAULT_SERVICE_CALL_NORM_SECONDS = 120


class ServiceCallRef(BaseModel):
    """A call the dispatcher must make to a service (issue #36, customer: «звено Б → В»):
    which service, which facts of the card must be passed, and how long the call may take."""

    model_config = SCENARIO

    service: str
    required_facts: list[str] = Field(default_factory=lambda: list(SERVICE_CALL_FACTS[:4]))
    norm_seconds: int = DEFAULT_SERVICE_CALL_NORM_SECONDS


class CardResponseReference(BaseModel):
    model_config = SCENARIO

    decision: Literal["accept", "reject"]
    reject_reason: str | None = None  # code from REJECT_REASONS when decision is reject
    status_chain: list[ReferenceStep]
    critical_errors: list[str] = Field(default_factory=list)
    # Calls to service officers the dispatcher is expected to make (empty = not evaluated).
    service_calls: list[ServiceCallRef] = Field(default_factory=list)


class InjectedError(BaseModel):
    """A mistake of the 112 operator planted in the card (issue #35): the card shows
    ``wrong_value``, the dispatcher is expected to flag the field and name ``correct_value``.
    ``field`` is a path into the card: ``address.house``, ``address.street``,
    ``incident_type``, ``flags.injured``, ``services``, ``caller.phone``, ``description``.
    For ``services`` the values are ``+code`` (a service that should not be notified) or
    ``-code`` (a service that is missing) — the wrong value describes what the card shows,
    the correct one what to do about it."""

    model_config = SCENARIO

    field: str
    wrong_value: str
    correct_value: str
    hint_level: int = 2  # 1 obvious, 2 noticeable, 3 subtle
    # Human-readable forms for the review (an incident type code → its title).
    wrong_label: str | None = None
    correct_label: str | None = None


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
    # Planted operator mistakes; the card keeps the wrong values, the truth lives only here.
    injected_errors: list[InjectedError] = Field(default_factory=list)
    # Replies of the service officers (issue #36), on top of the built-in ones per service;
    # generated ones wait for the teacher like the caller's (``approved``).
    service_replies: list[ServiceReply] = Field(default_factory=list)


class StatusEntry(BaseModel):
    """One row of ``attempts.status_log``."""

    model_config = STRICT

    status: str
    at: datetime
    order_number: str | None = None
    comment: str | None = None
    reject_reason: str | None = None  # code chosen in the drop-down for «Не принята» / «Отказ»


class FlaggedField(BaseModel):
    """A card field the dispatcher marked as wrong, with the value they consider right."""

    model_config = STRICT

    field: str
    corrected_value: str
    at: datetime


class ServiceCallLog(BaseModel):
    """One call of the dispatcher to a service officer (``attempts.service_calls[]``)."""

    model_config = STRICT

    service: str
    started_at: datetime
    answered: bool = False
    ended_at: datetime | None = None
    dialog: list[DialogTurn] = Field(default_factory=list)
    # Facts the live dialog counted as passed; the engine recomputes them from the turns.
    facts_passed: list[str] = Field(default_factory=list)


class CardResponseAttempt(BaseModel):
    model_config = STRICT

    issued_at: datetime  # «Добавлена»: the 30 seconds count from here
    received_at: datetime | None = None  # «Получена службой»
    status_log: list[StatusEntry] = Field(default_factory=list)
    flagged_fields: list[FlaggedField] = Field(default_factory=list)
    service_calls: list[ServiceCallLog] = Field(default_factory=list)


# --- call_intake -----------------------------------------------------------------------------


class CallerProfile(BaseModel):
    model_config = SCENARIO

    persona: str = "calm"
    voice: str | None = None
    noise: str | None = None
    opening: str = ""
    opening_audio: str | None = None  # studio recording of the opening (app.seed)
    facts: dict[str, str] = Field(default_factory=dict)
    behaviour: str | None = None
    drops_call: bool = False  # the caller hangs up before the operator finishes
    no_contact: bool = False  # nobody answers the call back


class ReplyVariant(BaseModel):
    """Another wording of an approved reply with the same facts; a call plays one of them
    at random so the caller does not sound like a recording."""

    model_config = SCENARIO

    text: str
    audio: str | None = None


class Reply(BaseModel):
    model_config = SCENARIO

    id: int
    topic: str
    text: str
    audio: str | None = None
    approved: bool = False
    variants: list[ReplyVariant] = Field(default_factory=list)


class ServiceReply(Reply):
    """A reply of a service officer (issue #36): which service says it; empty = any."""

    service: str | None = None


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
    # Blocking rule (issue #69): the trainee must ask at least this share of the required
    # topics, otherwise the attempt fails whatever the total. 0 disables the rule.
    min_questions_share: float = Field(default=0.5, ge=0.0, le=1.0)


class DialogTurn(BaseModel):
    """One row of ``attempts.dialog`` (or of a service call's dialog); ``topics`` come from
    the dialog engine when it knows them, otherwise the evaluation infers them from the text
    by keywords. In a service call the dispatcher is the ``operator`` and the officer the
    ``caller`` side, so the same engine serves both."""

    model_config = STRICT

    role: Literal["operator", "caller"]
    text: str
    topics: list[str] = Field(default_factory=list)
    at: datetime | None = None
    # How the turn was produced («cloud»: transcribed by the cloud voice provider, keyword
    # topics on free speech; local modes label a caller's reply with the topic it answers).
    method: str | None = None


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
