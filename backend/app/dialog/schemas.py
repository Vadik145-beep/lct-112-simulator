"""Request and response bodies of the caller dialog endpoints (PRD 9.3, 11)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    # Client-side id of the action: a retry after a connection loss returns the stored turn
    # instead of asking the caller twice.
    action_id: str | None = Field(default=None, max_length=64)


class AskTopicRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=40)
    action_id: str | None = Field(default=None, max_length=64)


class DialogTurnOut(BaseModel):
    index: int
    role: Literal["operator", "caller"]
    text: str
    topics: list[str] = Field(default_factory=list)
    at: datetime | None = None
    reply_id: int | None = None
    method: str | None = None
    audio_url: str | None = None
    generated: bool = False
    heard: bool = False  # operator turn came from speech recognition


class TopicOut(BaseModel):
    code: str
    title: str
    required: bool
    covered: bool


class CallOut(BaseModel):
    """The call of the attempt (PRD 9.5): idle | ringing | answered | ended."""

    state: str
    end_reason: str | None = None
    ended_at: datetime | None = None
    call_dropped_marked: bool = False
    no_contact_marked: bool = False
    # True when the call goes through Asterisk; False = browser microphone and /utterance.
    telephony: bool = False
    recording_available: bool = False


class DialogOut(BaseModel):
    attempt_id: str
    mode: str  # dialog mode actually in effect (buttons when no model is configured)
    requested_mode: str  # the lesson's dialog mode as the teacher set it
    # Caller's replies answered without the model (keywords or canned text) because it was
    # unreachable or its output was unusable; > 0 in a model mode = show a warning.
    fallback_replies: int
    stt_available: bool
    tts_available: bool
    answered_at: datetime | None
    call: CallOut
    turns: list[DialogTurnOut]
    topics: list[TopicOut]
    required_topics: list[str]
    seq: int


class TurnResponse(BaseModel):
    operator: DialogTurnOut
    caller: DialogTurnOut
    applied: bool  # False when the action_id was already answered (retry)
    pending_reply: bool  # hybrid: the caller's text is new and waits for the teacher
    latency_ms: int
    heard_text: str | None = None  # what speech recognition understood (utterance only)
    call_ended: bool = False  # the caller hung up right after this reply
    dialog: DialogOut
