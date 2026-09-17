"""What the generation model is asked to produce (PRD 9.6 item 2), and the JSON Schema files
of the stored scenario bodies.

The model never invents reference codes: the incident type, topics, personas, noises and
reject reasons are enums in the schema llama.cpp constrains the output with, and the
candidates for the incident type are injected per request from the classifier (top matches of
the phrase plus the type the teacher chose). Signs, expected services and the rest of the
reference card are derived from the chosen row by ``app.domain.scenarios.template``.

``schema_call_intake.json`` / ``schema_card_response.json`` next to this module are the schemas
of the *stored* bodies (``app.domain.evaluation.schemas``), exported for the documentation and
for clients that validate a body before ``PUT /scenarios/{id}``; a test keeps them in sync.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.evaluation.schemas import CallIntakeScenario, CardResponseScenario
from app.domain.reference_data import CALLER_TOPICS, REJECT_REASONS
from app.domain.scenarios.personas import NOISES, PERSONAS

SCHEMA_DIR = Path(__file__).parent
BODY_SCHEMA_FILES = {
    "call_intake": SCHEMA_DIR / "schema_call_intake.json",
    "card_response": SCHEMA_DIR / "schema_card_response.json",
}

TOPIC_CODES = tuple(t["code"] for t in CALLER_TOPICS)
PERSONA_CODES = tuple(p.code for p in PERSONAS)
NOISE_CODES = tuple(code for code, _ in NOISES)
REJECT_CODES = tuple(r["code"] for r in REJECT_REASONS)

MIN_REPLIES = 12
MAX_REPLIES = 25
MAX_REPLY_LENGTH = 220
MAX_TEXT_LENGTH = 600

STRICT = ConfigDict(extra="forbid")


class GeneratedAddress(BaseModel):
    model_config = STRICT

    region: str | None = None
    city: str | None = None
    street: str | None = None
    house: str | None = None
    building: str | None = None
    structure: str | None = None
    entrance: str | None = None
    floor: str | None = None
    code: str | None = None
    apartment: str | None = None
    descriptive: str | None = None


class GeneratedCaller(BaseModel):
    model_config = STRICT

    name: str | None = None
    role: str | None = None
    phone: str | None = None


class GeneratedReply(BaseModel):
    model_config = STRICT

    topic: Literal[TOPIC_CODES]  # type: ignore[valid-type]
    text: str = Field(min_length=1, max_length=MAX_REPLY_LENGTH)


class GeneratedCallIntake(BaseModel):
    """The model's answer for a call-intake scenario. ``incident_type`` is validated against
    the candidates of the request (see ``call_intake_schema``)."""

    model_config = STRICT

    title: str = Field(min_length=3, max_length=120)
    difficulty: int = Field(ge=1, le=3)
    persona: Literal[PERSONA_CODES]  # type: ignore[valid-type]
    noise: Literal[NOISE_CODES]  # type: ignore[valid-type]
    opening: str = Field(min_length=3, max_length=MAX_TEXT_LENGTH)
    facts: dict[str, str]
    behaviour: str = Field(max_length=MAX_TEXT_LENGTH)
    drops_call: bool = False
    replies: list[GeneratedReply] = Field(min_length=MIN_REPLIES, max_length=MAX_REPLIES)
    required_topics: list[Literal[TOPIC_CODES]] = Field(min_length=3)  # type: ignore[valid-type]
    incident_type: str
    flags: dict[str, bool]
    address: GeneratedAddress
    caller: GeneratedCaller
    description: str = Field(min_length=3, max_length=MAX_TEXT_LENGTH)
    description_keywords: list[str] = Field(min_length=2, max_length=8)


class GeneratedCardResponse(BaseModel):
    model_config = STRICT

    title: str = Field(min_length=3, max_length=120)
    difficulty: int = Field(ge=1, le=3)
    incident_type: str
    flags: dict[str, bool]
    address: GeneratedAddress
    caller: GeneratedCaller
    description: str = Field(min_length=3, max_length=MAX_TEXT_LENGTH)
    decision: Literal["accept", "reject"]
    reject_reason: Literal[REJECT_CODES] | None = None  # type: ignore[valid-type]
    # Comment examples for the reference chain: response started, works started, works done
    # (accept) or the single rejection comment (reject).
    comments: list[str] = Field(min_length=1, max_length=4)


def _with_type_enum(schema: dict[str, Any], candidates: list[str]) -> dict[str, Any]:
    schema = json.loads(json.dumps(schema))
    schema["properties"]["incident_type"] = {"type": "string", "enum": sorted(set(candidates))}
    return schema


def call_intake_schema(type_candidates: list[str]) -> dict[str, Any]:
    return _with_type_enum(GeneratedCallIntake.model_json_schema(), type_candidates)


def card_response_schema(type_candidates: list[str]) -> dict[str, Any]:
    return _with_type_enum(GeneratedCardResponse.model_json_schema(), type_candidates)


def body_schema(kind: str) -> dict[str, Any]:
    """JSON Schema of a stored body of the given kind."""
    model = {"call_intake": CallIntakeScenario, "card_response": CardResponseScenario}[kind]
    return model.model_json_schema()


def export_body_schemas() -> list[Path]:
    """Writes the body schemas next to this module (run by the test when they drift)."""
    written = []
    for kind, path in BODY_SCHEMA_FILES.items():
        path.write_text(
            json.dumps(body_schema(kind), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        written.append(path)
    return written
