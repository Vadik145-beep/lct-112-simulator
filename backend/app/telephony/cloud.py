"""Calls whose caller is played by Vapi (plan/track-c-vapi.md).

The trainee is rung exactly as in ``calls.CallManager``. When the trainee answers, instead
of the ExternalMedia spy and the local pipeline, the manager dials Vapi through the SIP
trunk of ``asterisk-cloud`` (``Local/s@vapi-out``) and bridges both legs; the bridge is
recorded as usual. Vapi identifies the call by the caller number of that leg, a one-off
numeric token, asks the backend which assistant to use (``assistant-request``) and reports
every final phrase (``transcript``); the phrases become ordinary dialog turns.

The call ends when the trainee hangs up (Asterisk event), when Vapi ends it (assistant hung
up, silence, maximum duration: ``status-update``) or when the SIP leg fails.
"""

from __future__ import annotations

import contextlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import call as call_state
from app.dialog import service as dialog
from app.domain.evaluation.text import normalize_text
from app.events import publish_events
from app.logging import get_logger
from app.models import (
    CALL_END_CALLER_HANGUP,
    CALL_END_FAILED,
    CALL_END_HANGUP,
    CALL_ENDED,
)
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriError
from app.telephony.calls import (
    CHANNEL_FORMAT,
    RECORDING_FORMAT,
    RECORDINGS_SUBDIR,
    VAR_ATTEMPT,
    Call,
    CallManager,
    load_attempt,
)
from app.telephony.vapi import VapiClient, VapiError, build_assistant, server_config

log = get_logger(__name__)

# Dialplan entry point of the Vapi leg and its channel variables
# (deploy/asterisk-cloud/extensions.conf).
VAPI_DIAL_ENDPOINT = "Local/s@vapi-out/n"
VAR_TOKEN = "__CALL_TOKEN"
VAR_VAPI_USER = "__VAPI_USER"
VAPI_RING_SECONDS = 30
TOKEN_DIGITS = 10
# Roles of Vapi's transcript → roles of the dialog.
TRANSCRIPT_ROLES = {"user": "operator", "assistant": "caller", "bot": "caller"}
ENDED_STATUS = "ended"
_DIGITS = re.compile(r"\d+")


def new_token() -> str:
    """Caller number of the Vapi leg: digits only, so any SIP stack passes it through."""
    return "".join(secrets.choice("0123456789") for _ in range(TOKEN_DIGITS))


@dataclass
class CloudCall(Call):
    token: str = ""
    vapi_channel_id: str | None = None
    vapi_call_id: str | None = None
    vapi_answered: bool = False

    def __post_init__(self) -> None:
        if not self.token:
            self.token = new_token()


class CloudCallManager(CallManager):
    call_class = CloudCall

    def __init__(self, ari, vapi: VapiClient | None = None) -> None:
        super().__init__(ari)
        self.vapi = vapi
        self.sip_user: str | None = None
        self._by_token: dict[str, CloudCall] = {}
        self._by_vapi_call: dict[str, CloudCall] = {}

    # ------------------------------------------------------------ set-up

    async def setup(self) -> bool:
        """Makes sure the Vapi account has the trainer's SIP number pointing at this stand.
        A failure is logged: calls then end as «failed» until the next start."""
        s = get_settings()
        if self.vapi is None or not s.cloud_voice_public_url:
            log.warning("cloud voice not configured: VAPI_API_KEY or CLOUD_VOICE_PUBLIC_URL")
            return False
        try:
            self.sip_user = await self.vapi.ensure_sip_number(
                s.vapi_number_name, s.vapi_sip_host, server_config(s)
            )
        except VapiError as exc:
            log.warning("vapi number set-up failed", error=str(exc))
            return False
        log.info("cloud voice ready", sip_user=self.sip_user, host=s.vapi_sip_host)
        return True

    # ------------------------------------------------------------ lookup

    async def dial(self, attempt_id):
        call = await super().dial(attempt_id)
        if isinstance(call, CloudCall):
            self._by_token[call.token] = call
        return call

    def call_for_token(self, token: str) -> CloudCall | None:
        return self._by_token.get(token)

    def call_for_message(self, message: dict) -> CloudCall | None:
        """The call a server message belongs to: by Vapi's call id once known, else by the
        caller number (our token) of the SIP leg."""
        vapi_call = message.get("call") or {}
        call_id = vapi_call.get("id")
        if call_id and call_id in self._by_vapi_call:
            return self._by_vapi_call[call_id]
        for candidate in _token_candidates(message):
            call = self._by_token.get(candidate)
            if call is not None:
                if call_id:
                    call.vapi_call_id = call_id
                    self._by_vapi_call[call_id] = call
                return call
        return None

    # ------------------------------------------------------------ ARI events

    async def handle_event(self, event: dict) -> None:
        kind = event.get("type")
        channel = event.get("channel") or {}
        channel_id = channel.get("id") or ""
        call = self._by_channel.get(channel_id)
        if isinstance(call, CloudCall) and channel_id == call.vapi_channel_id:
            if kind == "ChannelStateChange" and channel.get("state") == "Up":
                await self._on_vapi_answered(call)
            elif kind in ("StasisEnd", "ChannelDestroyed"):
                reason = CALL_END_CALLER_HANGUP if call.vapi_answered else CALL_END_FAILED
                await self._end(call, reason)
            return
        if kind == "Dial":
            peer = event.get("peer") or {}
            call = self._by_channel.get(peer.get("id") or "")
            if isinstance(call, CloudCall) and peer.get("id") == call.vapi_channel_id:
                status = event.get("dialstatus") or ""
                if status == "ANSWER":
                    await self._on_vapi_answered(call)
                elif status:
                    log.warning("vapi leg failed", attempt=str(call.attempt_id), status=status)
                    await self._end(call, CALL_END_FAILED)
                return
        await super().handle_event(event)

    async def _on_answered(self, call: Call) -> None:
        assert isinstance(call, CloudCall)
        async with call.lock:
            if call.answered or call.ended:
                return
            call.answered = True
        log.info("answered", attempt=str(call.attempt_id), cloud=True)
        async with SessionLocal() as session:
            config = await telephony_settings.load(session)
        try:
            if not self.sip_user:
                raise RuntimeError("Vapi number is not configured")
            call.bridge_id = f"br-{call.key}"
            await self.ari.create_bridge(call.bridge_id)
            await self.ari.add_channel(call.bridge_id, call.channel_id)
            if config.recording_enabled:
                call.recording_name = f"{RECORDINGS_SUBDIR}/{call.key}"
                await self.ari.record_bridge(call.bridge_id, call.recording_name, RECORDING_FORMAT)
            call.vapi_channel_id = f"vapi-{call.key}"
            self._by_channel[call.vapi_channel_id] = call
            await self.ari.create_channel(
                VAPI_DIAL_ENDPOINT,
                call.vapi_channel_id,
                app_args=f"vapi,{call.key}",
                formats=CHANNEL_FORMAT,
                variables={
                    VAR_TOKEN: call.token,
                    VAR_VAPI_USER: self.sip_user,
                    VAR_ATTEMPT: str(call.attempt_id),
                },
            )
            await self.ari.dial(call.vapi_channel_id, ring_seconds=VAPI_RING_SECONDS)
        except (AriError, OSError, RuntimeError) as exc:
            log.warning("cloud call setup failed", attempt=str(call.attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
            if loaded is None:
                await self._end(call, CALL_END_FAILED)
                return
            _, events = await call_state.answer(
                session, loaded.attempt, loaded.ts, loaded.version, loaded.scenario, telephony=True
            )
            if call.recording_name:
                loaded.attempt.recording_path = f"{call.recording_name}.{RECORDING_FORMAT}"
            await session.commit()
        await publish_events(events)

    async def _on_vapi_answered(self, call: CloudCall) -> None:
        async with call.lock:
            if call.vapi_answered or call.ended:
                return
            call.vapi_answered = True
        if not call.bridge_id or not call.vapi_channel_id:
            return
        try:
            await self.ari.add_channel(call.bridge_id, call.vapi_channel_id)
        except AriError as exc:
            log.warning("vapi leg not bridged", attempt=str(call.attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return
        log.info("vapi leg bridged", attempt=str(call.attempt_id))

    # ------------------------------------------------------------ server messages

    async def webhook(self, message: dict) -> dict[str, Any]:
        """One server message of Vapi; the answer body (an assistant for
        ``assistant-request``, empty for the rest)."""
        kind = message.get("type")
        call = self.call_for_message(message)
        if kind == "assistant-request":
            return await self._assistant_request(call)
        if call is None:
            log.info("vapi message for an unknown call", type=kind)
            return {}
        if kind == "transcript":
            if message.get("transcriptType", "final") == "final":
                role = TRANSCRIPT_ROLES.get(str(message.get("role")))
                if role:
                    await self._on_transcript(call, role, str(message.get("transcript") or ""))
        elif kind == "status-update":
            if message.get("status") == ENDED_STATUS:
                await self._end(call, _end_reason(str(message.get("endedReason") or "")))
        elif kind == "end-of-call-report":
            await self._on_report(call, message)
        elif kind == "hang":
            log.warning("vapi reports a delay", attempt=str(call.attempt_id))
        return {}

    async def _assistant_request(self, call: CloudCall | None) -> dict[str, Any]:
        if call is None:
            return {"error": "Учебный вызов не найден. Положите трубку и дождитесь нового звонка."}
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
        if loaded is None:
            return {"error": "Учебный вызов уже закрыт."}
        log.info("assistant for call", attempt=str(call.attempt_id), vapi_call=call.vapi_call_id)
        return {"assistant": build_assistant(loaded.scenario)}

    async def _on_transcript(self, call: CloudCall, role: str, text: str) -> None:
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
            if loaded is None or loaded.attempt.call_state == CALL_ENDED:
                return
            turn, events = await dialog.external_turn(
                session, loaded.attempt, loaded.ts, loaded.scenario, role, text
            )
            await session.commit()
        await publish_events(events)
        if turn is not None:
            log.info("cloud phrase", attempt=str(call.attempt_id), role=role, text=text)

    async def _on_report(self, call: CloudCall, message: dict) -> None:
        """The final transcript: phrases the live messages missed are added, then the call
        is ended if the status update did not arrive."""
        artifact = message.get("artifact") or {}
        reported = [
            (TRANSCRIPT_ROLES[m["role"]], str(m.get("message") or ""))
            for m in artifact.get("messages") or []
            if m.get("role") in TRANSCRIPT_ROLES
        ]
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
            if loaded is None:
                return
            attempt = loaded.attempt
            # The report starts with the opening Vapi spoke; turn 0 holds it already.
            if reported and attempt.dialog and reported[0][0] == "caller":
                if normalize_text(reported[0][1]) == normalize_text(attempt.dialog[0]["text"]):
                    reported = reported[1:]
            stored = len(dialog.external_turns(attempt))
            events = []
            if attempt.call_state != CALL_ENDED and len(reported) > stored:
                for role, text in reported[stored:]:
                    _, turn_events = await dialog.external_turn(
                        session, attempt, loaded.ts, loaded.scenario, role, text
                    )
                    events.extend(turn_events)
            await session.commit()
        await publish_events(events)
        await self._end(call, _end_reason(str(message.get("endedReason") or "")))

    # ------------------------------------------------------------ teardown

    async def _end(self, call: Call, reason: str, *, already_stored: bool = False) -> None:
        if isinstance(call, CloudCall):
            self._by_token.pop(call.token, None)
            if call.vapi_call_id:
                self._by_vapi_call.pop(call.vapi_call_id, None)
            if call.vapi_channel_id:
                self._by_channel.pop(call.vapi_channel_id, None)
                with contextlib.suppress(AriError):
                    await self.ari.hangup(call.vapi_channel_id)
        await super()._end(call, reason, already_stored=already_stored)


def _token_candidates(message: dict) -> list[str]:
    """Digit strings a server message may carry our token in: the caller number of the SIP
    leg (``customer.number`` / ``customer.sipUri``) and the template variables Vapi fills
    from ``x-`` SIP headers."""
    vapi_call = message.get("call") or {}
    values: list[str] = []
    for customer in (vapi_call.get("customer") or {}, message.get("customer") or {}):
        for key in ("number", "sipUri"):
            if customer.get(key):
                values.append(str(customer[key]))
    variables = (vapi_call.get("assistantOverrides") or {}).get("variableValues") or {}
    for key, value in variables.items():
        if "token" in key.lower():
            values.append(str(value))
    candidates: list[str] = []
    for value in values:
        for digits in _DIGITS.findall(value):
            if len(digits) >= TOKEN_DIGITS:
                candidates.append(digits[-TOKEN_DIGITS:])
    return candidates


def _end_reason(ended_reason: str) -> str:
    reason = ended_reason.lower()
    if reason.startswith("customer"):
        return CALL_END_HANGUP
    if reason.startswith("assistant") or "silence" in reason or "max-duration" in reason:
        return CALL_END_CALLER_HANGUP
    return CALL_END_FAILED
