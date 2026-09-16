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


class DialogOut(BaseModel):
    attempt_id: str
    mode: str  # dialog mode actually in effect (buttons when no model is reachable)
    stt_available: bool
    tts_available: bool
    answered_at: datetime | None
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
    dialog: DialogOut
