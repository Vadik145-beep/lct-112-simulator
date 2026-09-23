"""Bodies of the operator-112 card endpoints (PRD 11, 13.5): the draft that autosaves while
the call goes on and the final «сохранить»."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.training.schemas import AttemptOut

# «Описание со слов заявителя» is limited to 1999 characters in АРМ-112 (screenshot page 15).
DESCRIPTION_MAX = 1999


class AddressIn(BaseModel):
    """Address fields of the card as the operator fills them; empty strings mean «not set»."""

    model_config = ConfigDict(extra="forbid")

    region: str = Field(default="", max_length=120)
    city: str = Field(default="", max_length=120)
    okrug: str = Field(default="", max_length=40)
    district: str = Field(default="", max_length=120)
    street: str = Field(default="", max_length=200)
    house: str = Field(default="", max_length=20)
    building: str = Field(default="", max_length=20)
    structure: str = Field(default="", max_length=20)
    apartment: str = Field(default="", max_length=20)
    entrance: str = Field(default="", max_length=20)
    floor: str = Field(default="", max_length=20)
    code: str = Field(default="", max_length=20)
    # «Объект» of the screenshot: a named place (school, station) instead of a street.
    object: str = Field(default="", max_length=200)
    descriptive: str = Field(default="", max_length=500)


class CallerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="", max_length=200)
    role: str = Field(default="", max_length=60)
    phone: str = Field(default="", max_length=40)


class CardIn(BaseModel):
    """The card of the operator 112 (PRD 9.3, «SubmittedCard»); services carry what the
    interface resolved from the type and the flags plus the ones added by hand."""

    model_config = ConfigDict(extra="forbid")

    signs_path: list[str] = Field(default_factory=list, max_length=6)
    incident_type: str | None = Field(default=None, max_length=24)
    flags: dict[str, bool] = Field(default_factory=dict)
    # Asked in a small window when «Пострадавшие» is pressed, as on the live АРМ-112 (§5.2).
    injured_count: int | None = Field(default=None, ge=0, le=9999)
    services: list[str] = Field(default_factory=list, max_length=40)
    address: AddressIn = Field(default_factory=AddressIn)
    caller: CallerIn = Field(default_factory=CallerIn)
    description: str = Field(default="", max_length=DESCRIPTION_MAX)


class DraftRequest(BaseModel):
    card: CardIn
    # Client clock of the change, so a reload can tell the newer of local and server copies.
    updated_at: datetime | None = None


class DraftResponse(BaseModel):
    saved_at: datetime
    seq: int


class SubmitRequest(BaseModel):
    card: CardIn
    # Client-generated id: a retry after an outage returns the stored result, the card is
    # scored once (PRD 11: «идемпотентно по client_submission_id»).
    client_submission_id: str = Field(min_length=1, max_length=64)


class SubmitResponse(BaseModel):
    attempt: AttemptOut
    applied: bool  # False when this client_submission_id was already saved
    # The next call issued right after this one was saved (queue mode).
    issued: list[str]
