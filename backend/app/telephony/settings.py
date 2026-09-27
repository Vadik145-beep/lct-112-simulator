"""Telephony section of the operational settings (PRD 10 ``settings``, 11 ``/admin/settings``).

Defaults come from the environment (``app.config``); the administrator overrides them in the
``settings`` table. The administrator's screen is wave 9, the API is here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Setting

SETTINGS_KEY = "telephony"

# Codecs offered to the browser softphone and to a desk IP phone (PRD 9.5).
DEFAULT_WEBRTC_CODECS = ["opus", "g722", "alaw", "ulaw"]
DEFAULT_PHONE_CODECS = ["g722", "alaw", "ulaw"]
_CODEC_NAMES = {"opus", "g722", "alaw", "ulaw", "slin16", "slin"}


class TelephonySettings(BaseModel):
    """What the administrator can change; every field has a default."""

    enabled: bool
    ari_url: str
    ari_app: str
    ring_timeout_seconds: int = Field(ge=5, le=300)
    recording_enabled: bool
    webrtc_codecs: list[str]
    phone_codecs: list[str]
    sip_domain: str
    # Login of a trainee → his own phone (digits, 7XXXXXXXXXX), rung through MultiFon.
    trainee_phones: dict[str, str] = Field(default_factory=dict)


class TelephonySettingsPatch(BaseModel):
    ring_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    recording_enabled: bool | None = None
    webrtc_codecs: list[str] | None = None
    phone_codecs: list[str] | None = None
    ari_url: str | None = Field(default=None, max_length=300)
    trainee_phones: dict[str, str] | None = None


def normalize_phone(phone: str | None) -> str:
    """A Russian phone as MultiFon dials it: 11 digits starting with 7; empty when the input
    is not a phone. «8 922 …», «+7 (922) …» and a bare ten-digit number all become «7922…»."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) == 10:
        digits = "7" + digits
    elif len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    return digits if len(digits) == 11 and digits[0] == "7" else ""


def same_phone(a: str | None, b: str | None) -> bool:
    """Two numbers of one subscriber: the caller id may come as 8…, +7… or 7…."""
    left, right = normalize_phone(a), normalize_phone(b)
    return bool(left) and left == right


def parse_phones(raw: str) -> dict[str, str]:
    """``TELEPHONY_TRAINEE_PHONES``: «login=phone» pairs separated by commas."""
    phones: dict[str, str] = {}
    for pair in raw.split(","):
        login, _, phone = pair.partition("=")
        number = normalize_phone(phone)
        if login.strip() and number:
            phones[login.strip().lower()] = number
    return phones


def clean_phones(phones: dict[str, str]) -> dict[str, str]:
    """The administrator's table: logins in lower case, phones normalized, an empty phone
    removes the trainee; a phone that is not one is an error."""
    cleaned: dict[str, str] = {}
    for login, phone in phones.items():
        if not (phone or "").strip():
            continue
        number = normalize_phone(phone)
        if not number:
            raise ValueError(f"Телефон «{phone}» у {login}: нужен российский номер из 11 цифр.")
        cleaned[login.strip().lower()] = number
    return cleaned


def trainee_phone(config: TelephonySettings, login: str, own: str | None = None) -> str:
    """The trainee's own phone for MultiFon: the one he entered himself, else the
    administrator's table; empty when he has none."""
    return normalize_phone(own) or config.trainee_phones.get(login.lower(), "")


def phone_calls_available() -> bool:
    """Lessons may ring the trainees' phones: telephony is on and an operator line is
    configured, the trunk of the local Asterisk (docs/TRUNK.md) or MultiFon of asterisk-cloud."""
    s = get_settings()
    cloud_line = s.cloud_voice_enabled and s.multifon_user
    return bool(s.telephony_enabled and (s.telephony_trunk_user or cloud_line))


def defaults() -> TelephonySettings:
    s = get_settings()
    return TelephonySettings(
        enabled=s.telephony_enabled,
        ari_url=s.ari_url,
        ari_app=s.ari_app,
        ring_timeout_seconds=s.call_ring_timeout_seconds,
        recording_enabled=True,
        webrtc_codecs=list(DEFAULT_WEBRTC_CODECS),
        phone_codecs=list(DEFAULT_PHONE_CODECS),
        sip_domain=s.sip_domain,
        trainee_phones=parse_phones(s.telephony_trainee_phones),
    )


def merge(stored: dict | None) -> TelephonySettings:
    base = defaults().model_dump()
    for key, value in (stored or {}).items():
        if key in base and value is not None:
            base[key] = value
    return TelephonySettings.model_validate(base)


async def load(session: AsyncSession) -> TelephonySettings:
    row = await session.get(Setting, SETTINGS_KEY)
    return merge(row.value if row else None)


def clean_codecs(codecs: list[str]) -> list[str]:
    cleaned = [c.strip().lower() for c in codecs if c.strip()]
    unknown = [c for c in cleaned if c not in _CODEC_NAMES]
    if unknown:
        raise ValueError(f"Неизвестные кодеки: {', '.join(unknown)}.")
    if not cleaned:
        raise ValueError("Нужен хотя бы один кодек.")
    return cleaned


async def store(
    session: AsyncSession, patch: TelephonySettingsPatch, actor_id: uuid.UUID | None
) -> TelephonySettings:
    row = await session.get(Setting, SETTINGS_KEY)
    value = dict(row.value) if row else {}
    changes = patch.model_dump(exclude_none=True)
    for field in ("webrtc_codecs", "phone_codecs"):
        if field in changes:
            changes[field] = clean_codecs(changes[field])
    if "trainee_phones" in changes:
        changes["trainee_phones"] = clean_phones(changes["trainee_phones"])
    value.update(changes)
    if row is None:
        row = Setting(key=SETTINGS_KEY, value=value)
        session.add(row)
    else:
        row.value = value
    row.updated_by = actor_id
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return merge(value)
