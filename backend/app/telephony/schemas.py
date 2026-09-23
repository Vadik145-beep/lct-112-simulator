"""Bodies of the telephony endpoints (PRD 11): softphone credentials, the current call,
call control, administrator settings."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.dialog.schemas import DialogOut, DialogTurnOut


class SipAccountOut(BaseModel):
    """What the browser softphone needs to register (PRD 9.5). ``enabled`` false = no
    Asterisk on this stand, the panel uses the microphone instead."""

    enabled: bool
    connected: bool  # the backend is talking to Asterisk right now
    ws_path: str  # WebSocket path of SIP on this host (wss://<host><ws_path>)
    domain: str
    username: str  # stu-<login>
    password: str
    display_name: str
    phone_username: str  # phone-<login>, same password: for a desk IP phone


class CurrentCallOut(BaseModel):
    """The call-intake attempt that rings or is in progress for the trainee."""

    attempt_id: str
    session_id: str
    card_number: str
    call_state: str
    answered_at: datetime | None
    caller_number: str
    scenario_title: str
    seq: int


class AnswerResponse(BaseModel):
    # Пусто в облачном занятии: приветствие произносит Vapi, своей реплики у нас нет.
    opening: DialogTurnOut | None
    dialog: DialogOut


class CallResponse(BaseModel):
    dialog: DialogOut
