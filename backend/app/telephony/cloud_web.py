"""Cloud calls from the browser (plan/track-c-vapi.md, «без телефонии»).

With ``TELEPHONY_ENABLED=false`` there is no Asterisk: the trainee's browser talks to Vapi
directly (WebRTC, the Vapi Web SDK). For a call-intake attempt of a lesson in the ``cloud``
mode the backend stores an assistant of that scenario in the Vapi account (the caller's facts
stay out of the browser) and hands the browser its id, the public key and a token; the
browser starts the call with the token in the assistant overrides. Vapi's server messages
come to the same webhook as for the SIP calls and are handled by ``app.telephony.cloud``'s
shared part; Vapi records the call and the end-of-call report brings the file.

The call ends when the trainee hangs up in the panel (``/attempts/{id}/hangup`` and the SDK
stops the call), or when Vapi ends it (``status-update``). Either way the assistant is deleted.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import call as call_state
from app.domain.evaluation.schemas import CallIntakeScenario
from app.events import publish_events
from app.logging import get_logger
from app.models import CALL_END_FAILED, CALL_ENDED
from app.telephony.calls import (
    RECORDINGS_SUBDIR,
    LoadedAttempt,
    LoadedCardAttempt,
    load_attempt,
    load_card_attempt,
)
from app.telephony.cloud import (
    ENDED_STATUS,
    apply_report,
    end_reason,
    log_fallback,
    new_token,
    note_speech,
    record_transcript,
    report_recording_url,
    token_candidates,
)
from app.telephony.vapi import VapiClient, VapiError, build_assistant

log = get_logger(__name__)

RECORDING_PREFIX = "web-"


@dataclass
class WebCall:
    attempt_id: uuid.UUID
    session_id: uuid.UUID
    student_id: uuid.UUID
    assistant_id: str
    # Set for a call on the card of a dispatcher (issue #59): the cloud plays the duty
    # officer or the squad leader, and the phrases belong to that call, not to the card.
    service_call_id: str | None = None
    token: str = field(default_factory=new_token)
    vapi_call_id: str | None = None
    vapi_ended: bool = False
    ended: bool = False
    operator_stopped_at: float | None = None
    pending_latency_ms: int | None = None
    latencies_ms: list[int] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class WebCallStart:
    """What the browser needs to start the call."""

    public_key: str
    api_url: str
    assistant_id: str
    token: str


class CloudWebCalls:
    def __init__(self, vapi: VapiClient | None) -> None:
        self.vapi = vapi
        self.calls: dict[str, WebCall] = {}
        self._by_token: dict[str, WebCall] = {}
        self._by_assistant: dict[str, WebCall] = {}
        self._by_vapi_call: dict[str, WebCall] = {}

    # ------------------------------------------------------------ starting

    async def start(self, loaded: LoadedAttempt) -> WebCallStart:
        """An assistant of the attempt's scenario in the Vapi account and the keys of the
        browser call. ``VapiError`` when the cloud is unreachable or not configured."""
        return await self._prepare(
            loaded.attempt.id,
            loaded.attempt.session_id,
            loaded.attempt.student_id,
            loaded.scenario,
            key=loaded.attempt.id.hex,
        )

    async def start_service_call(self, loaded: LoadedCardAttempt, call_id: str) -> WebCallStart:
        """The same for a call on the card (issue #59): the cloud plays the other side of
        that call — the duty officer of a service or the leader of the squad — and the
        phrases go into its own transcript."""
        from app.dialog import officer

        record = officer.find_call(loaded.attempt, call_id)
        scenario = officer.call_scenario(loaded.scenario, record, loaded.attempt)
        return await self._prepare(
            loaded.attempt.id,
            loaded.attempt.session_id,
            loaded.attempt.student_id,
            scenario,
            key=call_id,
            service_call_id=call_id,
        )

    async def _prepare(
        self,
        attempt_id: uuid.UUID,
        session_id: uuid.UUID,
        student_id: uuid.UUID,
        scenario: CallIntakeScenario,
        *,
        key: str,
        service_call_id: str | None = None,
    ) -> WebCallStart:
        s = get_settings()
        if self.vapi is None or not s.vapi_public_key:
            raise VapiError("VAPI_API_KEY или VAPI_PUBLIC_KEY не заданы")
        if not s.cloud_voice_public_url:
            raise VapiError("CLOUD_VOICE_PUBLIC_URL не задан: Vapi некуда слать вебхуки")
        existing = self.calls.get(key)
        if existing is not None and not existing.ended:
            return self._start_of(existing, s)
        assistant = build_assistant(scenario, s, recording=True)
        assistant["name"] = f"web-{key[:12]}"
        assistant_id = await self.vapi.create_assistant(assistant)
        call = WebCall(
            attempt_id=attempt_id,
            session_id=session_id,
            student_id=student_id,
            assistant_id=assistant_id,
            service_call_id=service_call_id,
        )
        self.calls[key] = call
        self._by_token[call.token] = call
        self._by_assistant[assistant_id] = call
        log.info(
            "web call prepared",
            attempt=str(attempt_id),
            call=service_call_id,
            assistant=assistant_id,
        )
        return self._start_of(call, s)

    def _start_of(self, call: WebCall, s) -> WebCallStart:
        return WebCallStart(
            public_key=s.vapi_public_key or "",
            api_url=self.vapi.base_url if self.vapi is not None else s.vapi_api_url,
            assistant_id=call.assistant_id,
            token=call.token,
        )

    async def failed(self, attempt_id: uuid.UUID, reason: str) -> None:
        """The browser could not start or keep the call: the panel continues with the
        microphone and the stand-by provider; the session log says so."""
        call = self.calls.get(attempt_id.hex)
        if call is not None:
            await log_fallback(call, reason, session_id=call.session_id, student_id=call.student_id)
            await self._forget(call)
            return
        async with SessionLocal() as session:
            loaded = await load_attempt(session, attempt_id)
        if loaded is None:
            return
        stub = WebCall(
            attempt_id=attempt_id,
            session_id=loaded.attempt.session_id,
            student_id=loaded.attempt.student_id,
            assistant_id="",
        )
        await log_fallback(stub, reason, session_id=stub.session_id, student_id=stub.student_id)

    # ------------------------------------------------------------ lookup

    @staticmethod
    def key_of(call: WebCall) -> str:
        return call.service_call_id or call.attempt_id.hex

    def call_for_attempt(self, attempt_id: uuid.UUID) -> WebCall | None:
        return self.calls.get(attempt_id.hex)

    def call_for_message(self, message: dict) -> WebCall | None:
        vapi_call = message.get("call") or {}
        call_id = vapi_call.get("id")
        if call_id and call_id in self._by_vapi_call:
            return self._by_vapi_call[call_id]
        assistant_id = vapi_call.get("assistantId") or (message.get("assistant") or {}).get("id")
        call = self._by_assistant.get(assistant_id or "")
        if call is None:
            for candidate in token_candidates(message):
                call = self._by_token.get(candidate)
                if call is not None:
                    break
        if call is not None and call_id:
            call.vapi_call_id = call_id
            self._by_vapi_call[call_id] = call
        return call

    # ------------------------------------------------------------ server messages

    async def webhook(self, message: dict) -> dict[str, Any]:
        kind = message.get("type")
        call = self.call_for_message(message)
        if call is None or call.ended:
            log.info("vapi web message for an unknown call", type=kind)
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
            await self._save_recording(call, report_recording_url(message))
            await self._end(call, end_reason(str(message.get("endedReason") or "")))
        elif kind == "hang":
            log.warning("vapi reports a delay", attempt=str(call.attempt_id))
        return {}

    async def _save_recording(self, call: WebCall, url: str | None) -> None:
        if not url or self.vapi is None:
            return
        from app.dialog import service as dialog

        try:
            data = await self.vapi.download(url)
        except VapiError as exc:
            log.warning(
                "web call recording not fetched", attempt=str(call.attempt_id), error=str(exc)
            )
            return
        key = self.key_of(call)
        relative = f"{RECORDINGS_SUBDIR}/{RECORDING_PREFIX}{key}.wav"
        path = dialog.storage_root() / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        async with SessionLocal() as session:
            if call.service_call_id:
                from app.dialog import officer

                card = await load_card_attempt(session, call.attempt_id)
                if card is None:
                    return
                officer.set_recording(card.attempt, call.service_call_id, relative)
            else:
                loaded = await load_attempt(session, call.attempt_id)
                if loaded is None:
                    return
                loaded.attempt.recording_path = relative
            await session.commit()
        log.info("web call recorded", attempt=str(call.attempt_id), bytes=len(data))

    # ------------------------------------------------------------ ending

    async def hangup(self, attempt_id: uuid.UUID, service_call_id: str | None = None) -> bool:
        """The trainee ended the call in the panel (the SDK stops the media itself)."""
        call = self.calls.get(service_call_id or attempt_id.hex)
        if call is None:
            return False
        await self._forget(call)
        return True

    async def _end(self, call: WebCall, reason: str) -> None:
        async with call.lock:
            if call.ended:
                return
            call.ended = True
        async with SessionLocal() as session:
            events = []
            if call.service_call_id:
                from app.dialog import officer

                card = await load_card_attempt(session, call.attempt_id)
                if card is not None:
                    _, events = await officer.end(
                        session, card.attempt, call.service_call_id, reason
                    )
                    await session.commit()
            else:
                loaded = await load_attempt(session, call.attempt_id)
                if loaded is not None and loaded.attempt.call_state != CALL_ENDED:
                    events = await call_state.end(session, loaded.attempt, reason)
                    await session.commit()
        await publish_events(events)
        if call.latencies_ms:
            ordered = sorted(call.latencies_ms)
            log.info(
                "cloud latency",
                attempt=str(call.attempt_id),
                replies=len(ordered),
                median_ms=ordered[len(ordered) // 2],
                max_ms=ordered[-1],
            )
        log.info("web call ended", attempt=str(call.attempt_id), reason=reason)
        await self._forget(call)

    async def _forget(self, call: WebCall) -> None:
        call.ended = True
        self.calls.pop(self.key_of(call), None)
        self._by_token.pop(call.token, None)
        self._by_assistant.pop(call.assistant_id, None)
        if call.vapi_call_id:
            self._by_vapi_call.pop(call.vapi_call_id, None)
        if call.assistant_id and self.vapi is not None:
            try:
                await self.vapi.delete_assistant(call.assistant_id)
            except VapiError as exc:
                log.warning(
                    "web assistant not deleted", assistant=call.assistant_id, error=str(exc)
                )

    async def shutdown(self) -> None:
        for call in list(self.calls.values()):
            await self._end(call, CALL_END_FAILED)
        if self.vapi is not None:
            await self.vapi.aclose()


# ---------------------------------------------------------------- life cycle

_calls: CloudWebCalls | None = None


def get_calls() -> CloudWebCalls | None:
    return _calls


def web_calls_active() -> bool:
    """True when browser calls of the cloud mode can be started here (no telephony)."""
    from app.telephony.service import telephony_active

    s = get_settings()
    return (
        _calls is not None
        and not telephony_active()
        and bool(s.vapi_public_key)
        and bool(s.cloud_voice_public_url)
    )


async def start() -> None:
    global _calls
    s = get_settings()
    if not s.cloud_voice_enabled or _calls is not None:
        return
    vapi = VapiClient(s.vapi_api_url, s.vapi_api_key) if s.vapi_api_key else None
    _calls = CloudWebCalls(vapi)
    log.info("cloud web calls ready", public_key=bool(s.vapi_public_key))


async def stop() -> None:
    global _calls
    if _calls is None:
        return
    await _calls.shutdown()
    _calls = None
