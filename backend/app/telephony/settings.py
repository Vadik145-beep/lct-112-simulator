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


class TelephonySettingsPatch(BaseModel):
    ring_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    recording_enabled: bool | None = None
    webrtc_codecs: list[str] | None = None
    phone_codecs: list[str] | None = None
    ari_url: str | None = Field(default=None, max_length=300)


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
