"""Calls whose caller is played by Vapi (plan/track-c-vapi.md).

The trainee is rung exactly as in ``calls.CallManager``. When the trainee answers a call of a
lesson in the ``cloud`` dialog mode, instead of the ExternalMedia spy and the local pipeline
the manager dials Vapi through the SIP trunk of ``asterisk-cloud`` (``Local/s@vapi-out``) and
bridges both legs; the bridge is recorded as usual. Vapi identifies the call by the caller
number of that leg, a one-off numeric token, asks the backend which assistant to use
(``assistant-request``) and reports every final phrase (``transcript``); the phrases become
ordinary dialog turns. Lessons in the other modes run the local conversation as before.

When the cloud does not answer (the SIP leg fails or rings out) or its leg drops without
Vapi ending the call, the same call continues on the local pipeline with the lesson's
stand-by provider (``select``); the session log gets ``call.cloud_fallback``.

The call ends when the trainee hangs up (Asterisk event), when Vapi ends it (assistant hung
up, silence, maximum duration: ``status-update``) or when nothing can play the caller.

The calls of the card (a service officer, a squad's report, a call back to the caller) go
the same way in a cloud lesson: the trainee's phone rings from the other side's number, and
after the answer the Vapi leg plays the officer, the squad leader or the caller
(``officer.call_scenario``); the phrases go into that call's own transcript. When the cloud
fails, the local officer pipeline takes the call over with its greeting.

``CloudTurns`` — the part shared with the browser calls (``app.telephony.cloud_web``): the
transcript, the reply latency from ``speech-update`` and the final report.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import service as dialog
from app.domain.evaluation.text import normalize_text
from app.events import append_event, publish_events
from app.logging import get_logger
from app.models import (
    CALL_END_CALLER_HANGUP,
    CALL_END_FAILED,
    CALL_END_HANGUP,
    CALL_END_SILENCE,
    CALL_ENDED,
)
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriError
from app.telephony.calls import (
    CHANNEL_FORMAT,
    RECORDING_FORMAT,
    VAR_ATTEMPT,
    Call,
    CallManager,
    load_attempt,
    load_card_attempt,
)
from app.telephony.vapi import VapiClient, VapiError, build_assistant, server_config

log = get_logger(__name__)

# Dialplan entry point of the Vapi leg and its channel variables
# (deploy/asterisk-cloud/extensions.conf).
VAPI_DIAL_ENDPOINT = "Local/s@vapi-out/n"
VAR_TOKEN = "__CALL_TOKEN"  # noqa: S105 - channel variable name, not a secret
VAR_VAPI_USER = "__VAPI_USER"
TOKEN_DIGITS = 10
# Roles of Vapi's transcript → roles of the dialog.
TRANSCRIPT_ROLES = {"user": "operator", "assistant": "caller", "bot": "caller"}
ENDED_STATUS = "ended"
# Seconds to wait for Vapi's ``status-update`` after its SIP leg dropped before treating the
# drop as a failure of the cloud (Vapi hanging up on purpose reports itself within a second).
LEG_LOST_GRACE_SECONDS = 3.0
EVENT_CLOUD_FALLBACK = "call.cloud_fallback"
_DIGITS = re.compile(r"\d+")


def new_token() -> str:
    """Caller number of the Vapi leg: digits only, so any SIP stack passes it through."""
    return "".join(secrets.choice("0123456789") for _ in range(TOKEN_DIGITS))


class CloudTurns(Protocol):
    """What the shared transcript handling needs of a call, SIP or browser. A call of the
    card (``service_call_id``) stores its phrases in that call, not in the card's own
    dialog: the dispatcher rings the duty officer, the squad leader rings the dispatcher."""

    attempt_id: uuid.UUID
    service_call_id: str | None
    vapi_call_id: str | None
    vapi_ended: bool
    operator_stopped_at: float | None
    pending_latency_ms: int | None
    latencies_ms: list[int]


@dataclass
class CloudCall(Call):
    token: str = ""
    vapi_channel_id: str | None = None
    vapi_call_id: str | None = None
    vapi_answered: bool = False
    # Vapi reported the end itself (status-update / report): its leg dropping is no failure.
    vapi_ended: bool = False
    # The local pipeline took the call over (the cloud failed).
    local: bool = False
    # Reply latency: the operator's last phrase ended at …, the next reply started … later.
    operator_stopped_at: float | None = None
    pending_latency_ms: int | None = None
    latencies_ms: list[int] = field(default_factory=list)
    leg_lost: asyncio.Task | None = None

    def __post_init__(self) -> None:
        if not self.token:
            self.token = new_token()


# ---------------------------------------------------------------- shared server messages


def note_speech(call: CloudTurns, message: dict) -> int | None:
    """``speech-update``: the operator stopped → the clock starts; the caller started → the
    latency of the coming reply. Returns the latency when one was measured."""
    role, status = str(message.get("role")), str(message.get("status"))
    now = time.monotonic()
    if role == "user" and status == "stopped":
        call.operator_stopped_at = now
    elif role == "assistant" and status == "started" and call.operator_stopped_at is not None:
        latency = int((now - call.operator_stopped_at) * 1000)
        call.operator_stopped_at = None
        call.pending_latency_ms = latency
        call.latencies_ms.append(latency)
        return latency
    return None


async def record_transcript(call: CloudTurns, message: dict) -> dict | None:
    """A final ``transcript`` message as a dialog turn (partial ones are skipped)."""
    if message.get("transcriptType", "final") != "final":
        return None
    role = TRANSCRIPT_ROLES.get(str(message.get("role")))
    text = str(message.get("transcript") or "")
    if not role:
        return None
    latency = call.pending_latency_ms if role == "caller" else None
    if call.service_call_id:
        return await _record_service_transcript(call, role, text, latency)
    async with SessionLocal() as session:
        loaded = await load_attempt(session, call.attempt_id)
        if loaded is None or loaded.attempt.call_state == CALL_ENDED:
            return None
        turn, events = await dialog.external_turn(
            session, loaded.attempt, loaded.ts, loaded.scenario, role, text, latency_ms=latency
        )
        await session.commit()
    await publish_events(events)
    if turn is not None:
        if role == "caller":
            call.pending_latency_ms = None
        log.info(
            "cloud phrase", attempt=str(call.attempt_id), role=role, text=text, latency_ms=latency
        )
    return turn


async def _record_service_transcript(
    call: CloudTurns, role: str, text: str, latency: int | None
) -> dict | None:
    """The phrase of a call on the card: it belongs to that call's transcript."""
    from app.dialog import officer

    async with SessionLocal() as session:
        loaded = await load_card_attempt(session, call.attempt_id)
        if loaded is None:
            return None
        assert call.service_call_id is not None
        turn, events = await officer.external_turn(
            session,
            loaded.attempt,
            call.service_call_id,
            loaded.scenario,
            role,
            text,
            latency_ms=latency,
        )
        await session.commit()
    await publish_events(events)
    if turn is not None and role == "caller":
        call.pending_latency_ms = None
    if turn is not None:
        log.info(
            "cloud phrase",
            attempt=str(call.attempt_id),
            call=call.service_call_id,
            role=role,
            text=text,
            latency_ms=latency,
        )
    return turn


async def apply_report(call: CloudTurns, message: dict) -> None:
    """The final transcript of ``end-of-call-report``: phrases the live messages missed are
    added; stored turns are never rewritten."""
    artifact = message.get("artifact") or {}
    reported = [
        (TRANSCRIPT_ROLES[m["role"]], str(m.get("message") or ""))
        for m in artifact.get("messages") or []
        if m.get("role") in TRANSCRIPT_ROLES
    ]
    if call.service_call_id:
        await _apply_service_report(call, reported)
        return
    async with SessionLocal() as session:
        loaded = await load_attempt(session, call.attempt_id)
        if loaded is None:
            return
        attempt = loaded.attempt
        # The report starts with the opening Vapi spoke. When the cloud worked, that phrase
        # is already turn 0 of the transcript and is counted in ``stored``; only our own
        # opening (a lesson that fell back to the local pipeline) has to be skipped here.
        ours = bool(attempt.dialog) and attempt.dialog[0].get("method") == "opening"
        if reported and ours and reported[0][0] == "caller":
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


async def _apply_service_report(call: CloudTurns, reported: list[tuple[str, str]]) -> None:
    """Same for a call on the card: only the phrases the live messages missed are added."""
    from app.dialog import officer

    async with SessionLocal() as session:
        loaded = await load_card_attempt(session, call.attempt_id)
        if loaded is None:
            return
        assert call.service_call_id is not None
        record = officer.find_call(loaded.attempt, call.service_call_id)
        stored = len(
            [t for t in record.get("dialog") or [] if t.get("method") == dialog.EXTERNAL_METHOD]
        )
        events = []
        if not record.get("ended_at") and len(reported) > stored:
            for role, text in reported[stored:]:
                _, turn_events = await officer.external_turn(
                    session,
                    loaded.attempt,
                    call.service_call_id,
                    loaded.scenario,
                    role,
                    text,
                )
                events.extend(turn_events)
        await session.commit()
    await publish_events(events)


def report_recording_url(message: dict) -> str | None:
    artifact = message.get("artifact") or {}
    return artifact.get("recordingUrl") or message.get("recordingUrl") or None


def end_reason(ended_reason: str) -> str:
    reason = ended_reason.lower()
    if reason.startswith("customer"):
        return CALL_END_HANGUP
    if "silence" in reason or "max-duration" in reason:
        # Nobody hung up: Vapi closed the call itself. Saying «заявитель положил трубку»
        # misleads the trainee, who was only filling the card (замечание 22.09.2026).
        return CALL_END_SILENCE
    if reason.startswith("assistant"):
        return CALL_END_CALLER_HANGUP
    return CALL_END_FAILED


def token_candidates(message: dict) -> list[str]:
    """Digit strings a server message may carry our token in: the caller number of the SIP
    leg (``customer.number`` / ``customer.sipUri``) and the template variables Vapi fills
    from ``x-`` SIP headers or the browser's overrides."""
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


async def log_fallback(
    call: CloudTurns, reason: str, *, session_id: uuid.UUID, student_id: uuid.UUID
) -> None:
    """``call.cloud_fallback`` in the session log: the teacher's monitor and the review see
    that the cloud was unavailable and what answered instead."""
    async with SessionLocal() as session:
        event = await append_event(
            session,
            session_id=session_id,
            type_=EVENT_CLOUD_FALLBACK,
            student_id=student_id,
            payload={"attempt_id": call.attempt_id, "reason": reason},
        )
        await session.commit()
    await publish_events([event])


# ---------------------------------------------------------------- the SIP manager


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
        A failure is logged: cloud calls then run on the local pipeline until the next start."""
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

    def _dialled(self, call: Call) -> None:
        if isinstance(call, CloudCall):
            self._by_token[call.token] = call

    def call_for_token(self, token: str) -> CloudCall | None:
        return self._by_token.get(token)

    def call_for_message(self, message: dict) -> CloudCall | None:
        """The call a server message belongs to: by Vapi's call id once known, else by the
        caller number (our token) of the SIP leg."""
        vapi_call = message.get("call") or {}
        call_id = vapi_call.get("id")
        if call_id and call_id in self._by_vapi_call:
            return self._by_vapi_call[call_id]
        for candidate in token_candidates(message):
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
                await self._on_vapi_leg_gone(call)
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
                    await self._fallback(call, f"vapi leg {status.lower()}")
                return
        await super().handle_event(event)

    async def _on_answered(self, call: Call) -> None:
        assert isinstance(call, CloudCall)
        async with call.lock:
            if call.answered or call.ended:
                return
            call.answered = True
        async with SessionLocal() as session:
            config = await telephony_settings.load(session)
            if call.to_officer:
                loaded = await load_card_attempt(session, call.attempt_id)
            else:
                loaded = await load_attempt(session, call.attempt_id)
            cloud = loaded is not None and dialog.cloud_lesson(loaded.ts)
        if not cloud:
            log.info("answered", attempt=str(call.attempt_id), call=call.service_call_id)
            await self._run_local(call, config)
            return
        log.info("answered", attempt=str(call.attempt_id), call=call.service_call_id, cloud=True)
        try:
            await self._open_bridge(call, config)
        except (AriError, OSError, RuntimeError) as exc:
            log.warning("cloud call setup failed", attempt=str(call.attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return
        try:
            if not self.sip_user:
                raise RuntimeError("Vapi number is not configured")
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
            await self.ari.dial(
                call.vapi_channel_id, ring_seconds=get_settings().cloud_voice_answer_seconds
            )
        except (AriError, OSError, RuntimeError) as exc:
            log.warning("vapi leg not dialled", attempt=str(call.attempt_id), error=str(exc))
            await self._fallback(call, str(exc))
            return
        if call.to_officer:
            # The record is answered when Vapi picks up: a failed leg still gets the local
            # officer's greeting.
            return
        # The state «в разговоре»; the opening is Vapi's first message, nothing to play.
        await self._speak_opening(call, play=False)

    async def _on_vapi_answered(self, call: CloudCall) -> None:
        async with call.lock:
            if call.vapi_answered or call.ended or call.local:
                return
            call.vapi_answered = True
        if not call.bridge_id or not call.vapi_channel_id:
            return
        try:
            await self.ari.add_channel(call.bridge_id, call.vapi_channel_id)
        except AriError as exc:
            log.warning("vapi leg not bridged", attempt=str(call.attempt_id), error=str(exc))
            await self._fallback(call, "vapi leg not bridged")
            return
        log.info("vapi leg bridged", attempt=str(call.attempt_id), call=call.service_call_id)
        if call.to_officer:
            await self._service_answered_by_cloud(call)

    async def _service_answered_by_cloud(self, call: CloudCall) -> None:
        """The other side of a call of the card picked up in the cloud: the record is answered
        without our greeting (Vapi says the first phrase) and gets the recording."""
        from app.dialog import officer

        assert call.service_call_id is not None
        async with SessionLocal() as session:
            loaded = await load_card_attempt(session, call.attempt_id)
            if loaded is None:
                return
            _, events = await officer.answer(
                session,
                loaded.attempt,
                loaded.ts,
                loaded.version,
                loaded.scenario,
                call.service_call_id,
                with_opening=False,
            )
            if call.recording_name:
                officer.set_recording(
                    loaded.attempt,
                    call.service_call_id,
                    f"{call.recording_name}.{RECORDING_FORMAT}",
                )
            await session.commit()
        await publish_events(events)

    async def _on_vapi_leg_gone(self, call: CloudCall) -> None:
        """The SIP leg to Vapi is over. Vapi ending the call reports itself (``status-update``)
        around the same moment; when no report comes, the cloud dropped and the local pipeline
        takes over."""
        if call.ended or call.local:
            return
        if call.vapi_ended or not call.vapi_answered:
            reason = CALL_END_CALLER_HANGUP if call.vapi_answered else CALL_END_FAILED
            if not call.vapi_answered:
                await self._fallback(call, "vapi leg dropped before the answer")
                return
            await self._end(call, reason)
            return
        if call.leg_lost is None:
            call.leg_lost = asyncio.create_task(self._leg_lost_after_grace(call))

    async def _leg_lost_after_grace(self, call: CloudCall) -> None:
        await asyncio.sleep(LEG_LOST_GRACE_SECONDS)
        if call.ended or call.vapi_ended or call.local:
            return
        await self._fallback(call, "vapi leg dropped mid-call")

    async def _fallback(self, call: CloudCall, reason: str) -> None:
        """The local pipeline takes the same call: the spy and the stand-by provider of the
        lesson; the opening is played when Vapi never got to say it."""
        async with call.lock:
            if call.ended or call.local:
                return
            call.local = True
        log.warning(
            "cloud voice unavailable, local pipeline takes over",
            attempt=str(call.attempt_id),
            reason=reason,
        )
        heard_opening = call.vapi_answered
        await self._drop_vapi_leg(call)
        if not call.bridge_id:
            await self._end(call, CALL_END_FAILED)
            return
        try:
            await self._start_spy(call)
        except (AriError, OSError, RuntimeError) as exc:
            log.warning("local pipeline failed", attempt=str(call.attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return
        await log_fallback(call, reason, session_id=call.session_id, student_id=call.student_id)
        if call.to_officer:
            # Vapi never answered: the local officer greets. It did: the record is answered
            # already, the conversation just goes on locally.
            if not heard_opening:
                await self._officer_answered(call)
            return
        await self._speak_opening(call, play=not heard_opening)

    async def _drop_vapi_leg(self, call: CloudCall) -> None:
        self._by_token.pop(call.token, None)
        if call.vapi_call_id:
            self._by_vapi_call.pop(call.vapi_call_id, None)
        if call.vapi_channel_id:
            self._by_channel.pop(call.vapi_channel_id, None)
            with contextlib.suppress(AriError):
                await self.ari.hangup(call.vapi_channel_id)
        if call.leg_lost is not None and call.leg_lost is not asyncio.current_task():
            call.leg_lost.cancel()
            call.leg_lost = None

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
        if call.local:
            return {}
        if kind == "transcript":
            await record_transcript(call, message)
        elif kind == "speech-update":
            note_speech(call, message)
        elif kind == "status-update":
            if message.get("status") == ENDED_STATUS:
                call.vapi_ended = True
                await self._end(call, end_reason(str(message.get("endedReason") or "")))
        elif kind == "end-of-call-report":
            call.vapi_ended = True
            await apply_report(call, message)
            await self._end(call, end_reason(str(message.get("endedReason") or "")))
        elif kind == "hang":
            log.warning("vapi reports a delay", attempt=str(call.attempt_id))
        return {}

    async def _assistant_request(self, call: CloudCall | None) -> dict[str, Any]:
        if call is None:
            return {"error": "Учебный вызов не найден. Положите трубку и дождитесь нового звонка."}
        if call.service_call_id:
            return await self._service_assistant(call)
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
        if loaded is None:
            return {"error": "Учебный вызов уже закрыт."}
        log.info("assistant for call", attempt=str(call.attempt_id), vapi_call=call.vapi_call_id)
        return {"assistant": build_assistant(loaded.scenario)}

    async def _service_assistant(self, call: CloudCall) -> dict[str, Any]:
        """The other side of a call of the card: the officer, the squad leader or the caller
        rung back, built as the browser call builds it (``cloud_web.start_service_call``)."""
        from app.dialog import officer

        assert call.service_call_id is not None
        async with SessionLocal() as session:
            loaded = await load_card_attempt(session, call.attempt_id)
        if loaded is None:
            return {"error": "Карточка уже закрыта."}
        record = officer.find_call(loaded.attempt, call.service_call_id)
        if record.get("ended_at"):
            return {"error": "Звонок уже завершён."}
        scenario = officer.call_scenario(loaded.scenario, record, loaded.attempt)
        log.info(
            "assistant for call",
            attempt=str(call.attempt_id),
            call=call.service_call_id,
            vapi_call=call.vapi_call_id,
        )
        return {"assistant": build_assistant(scenario)}

    # ------------------------------------------------------------ teardown

    async def _end(self, call: Call, reason: str, *, already_stored: bool = False) -> None:
        if isinstance(call, CloudCall):
            await self._drop_vapi_leg(call)
            if call.latencies_ms:
                ordered = sorted(call.latencies_ms)
                log.info(
                    "cloud latency",
                    attempt=str(call.attempt_id),
                    replies=len(ordered),
                    median_ms=ordered[len(ordered) // 2],
                    max_ms=ordered[-1],
                )
        await super()._end(call, reason, already_stored=already_stored)
