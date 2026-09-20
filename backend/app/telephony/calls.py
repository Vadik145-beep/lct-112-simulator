"""Calls of the trainer through Asterisk (PRD 9.5).

One ``Call`` per call-intake attempt. The backend dials ``Local/s@trainer-out`` (the dialplan
rings the trainee's softphone and desk phone, ``deploy/asterisk/extensions.conf``), and when
the trainee answers:

1. the channel goes into a mixing bridge that is recorded to ``storage/recordings``;
2. a snoop channel spies what the trainee says and, bridged with an ExternalMedia channel,
   streams it as RTP to this process; ``media.Segmenter`` cuts phrases, ``STTProvider``
   turns them into text, ``dialog.say`` gets the caller's reply;
3. the reply's voice file (``sln16``) is played into the bridge; the scenario's opening is
   played first.

Events of the call (``call.ringing``, ``call.answered``, ``call.ended``) and every dialog turn
go into the session log like everything else, so the panel and the monitoring see them.

A dispatcher's call to a service officer (issue #36) uses the same legs the other way round:
``dial_service`` rings the trainee's phones from the officer's number (``Local/s@service-out``,
header ``X-Service-Call`` so the softphone answers at once), and after the answer the officer
greets and the same pipeline (snoop → STT → ``officer.say`` → voice) runs on the call's own
record in ``attempts.service_calls``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import call as call_state
from app.dialog import officer
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CallIntakeScenario, CardResponseScenario
from app.events import get_redis, publish_events
from app.logging import get_logger
from app.models import (
    ACTIVE_ATTEMPT_STATES,
    CALL_END_CALLER_HANGUP,
    CALL_END_FAILED,
    CALL_END_HANGUP,
    CALL_END_NO_ANSWER,
    CALL_ENDED,
    CALL_IDLE,
    MODE_CALL_INTAKE,
    MODE_CARD_RESPONSE,
    SESSION_RUNNING,
    Attempt,
    ScenarioVersion,
    TrainingSession,
    User,
)
from app.providers.stt import get_stt_provider
from app.telephony import media, sip
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriClient, AriError
from app.training import service as training

log = get_logger(__name__)

# Dialplan entry point and the channel variables it reads (deploy/asterisk/extensions.conf).
DIAL_ENDPOINT = "Local/s@trainer-out/n"
SERVICE_DIAL_ENDPOINT = "Local/s@service-out/n"
VAR_LOGIN = "__STU_LOGIN"
VAR_ATTEMPT = "__ATTEMPT_ID"
VAR_SERVICE_CALL = "__SERVICE_CALL_ID"
VAR_RING_TIMEOUT = "__RING_TIMEOUT"
VAR_CALLER_NAME = "__CALLER_NAME"
VAR_CALLER_NUM = "__CALLER_NUM"
DEFAULT_CALLER_NUMBER = "112"
CHANNEL_FORMAT = "slin16"
RECORDINGS_SUBDIR = "recordings"
RECORDING_FORMAT = "wav"
PLAYBACK_TIMEOUT_SECONDS = 120
DIAL_LOCK_SECONDS = 60
# Dial statuses of Asterisk → why the call ended without an answer.
_DIAL_FAILED = {
    "NOANSWER": CALL_END_NO_ANSWER,
    "CANCEL": CALL_END_NO_ANSWER,
    "BUSY": CALL_END_FAILED,
    "CONGESTION": CALL_END_FAILED,
    "CHANUNAVAIL": CALL_END_FAILED,
    "INVALIDARGS": CALL_END_FAILED,
}


@dataclass
class Call:
    attempt_id: uuid.UUID
    session_id: uuid.UUID
    student_id: uuid.UUID
    login: str
    channel_id: str
    bridge_id: str | None = None
    spy_bridge_id: str | None = None
    snoop_id: str | None = None
    media_id: str | None = None
    port: int | None = None
    receiver: media.RtpReceiver | None = None
    recording_name: str | None = None
    answered: bool = False
    ended: bool = False
    in_stasis: set[str] = field(default_factory=set)
    playbacks: dict[str, asyncio.Future] = field(default_factory=dict)
    pipeline: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    started_at: float = field(default_factory=time.monotonic)
    # Latency of every reply: seconds from the end of the phrase to the start of playback.
    reply_latencies: list[float] = field(default_factory=list)
    # Set for a dispatcher's call to a service officer (issue #36); None for a 112 call.
    service_call_id: str | None = None
    service: str | None = None
    service_title: str = ""

    @property
    def key(self) -> str:
        return self.service_call_id or self.attempt_id.hex

    @property
    def to_officer(self) -> bool:
        return self.service_call_id is not None


@dataclass
class LoadedAttempt:
    attempt: Attempt
    ts: TrainingSession
    version: ScenarioVersion
    scenario: CallIntakeScenario


async def load_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> LoadedAttempt | None:
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or attempt.mode != MODE_CALL_INTAKE:
        return None
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    return LoadedAttempt(attempt, ts, card.version, CallIntakeScenario.model_validate(card.body))


@dataclass
class LoadedCardAttempt:
    attempt: Attempt
    ts: TrainingSession
    version: ScenarioVersion
    scenario: CardResponseScenario


async def load_card_attempt(
    session: AsyncSession, attempt_id: uuid.UUID
) -> LoadedCardAttempt | None:
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or attempt.mode != MODE_CARD_RESPONSE:
        return None
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    return LoadedCardAttempt(
        attempt, ts, card.version, CardResponseScenario.model_validate(card.body)
    )


def service_number(service: str) -> str:
    """A stable three-digit «extension» of a service's officer for the caller id."""
    digest = hashlib.sha1(service.encode()).hexdigest()  # noqa: S324 - not security
    return str(200 + int(digest[:4], 16) % 700)


class CallManager:
    # The record of one call; the cloud manager (app.telephony.cloud) extends it.
    call_class: type[Call] = Call

    def __init__(self, ari: AriClient) -> None:
        self.ari = ari
        s = get_settings()
        self.ports = media.PortPool(s.telephony_media_port_start, s.telephony_media_port_end)
        self.vad_model = s.vad_model_path
        self.calls: dict[str, Call] = {}
        self._by_channel: dict[str, Call] = {}
        # Set by the service: rewrites the endpoints file after a new SIP account.
        self.sync_endpoints: Callable[[], Awaitable[None]] | None = None

    # ------------------------------------------------------------ lookup

    def call_for_attempt(self, attempt_id: uuid.UUID) -> Call | None:
        return self.calls.get(attempt_id.hex)

    def call_for_service_call(self, call_id: str) -> Call | None:
        return self.calls.get(call_id)

    def _call_for_channel(self, channel_id: str | None) -> Call | None:
        return self._by_channel.get(channel_id or "")

    def _media_host(self) -> str:
        host = get_settings().telephony_media_host
        if host:
            return host
        import socket

        return socket.gethostname()

    # ------------------------------------------------------------ dialling

    async def dial_pending(self) -> int:
        """Calls for call-intake attempts issued while telephony was down (start-up)."""
        from sqlalchemy import select

        async with SessionLocal() as session:
            rows = await session.execute(
                select(Attempt.id)
                .join(TrainingSession, TrainingSession.id == Attempt.session_id)
                .where(
                    Attempt.mode == MODE_CALL_INTAKE,
                    Attempt.call_state == CALL_IDLE,
                    Attempt.state.in_(ACTIVE_ATTEMPT_STATES),
                    TrainingSession.status == SESSION_RUNNING,
                )
            )
            ids = [row[0] for row in rows]
        for attempt_id in ids:
            await self.dial(attempt_id)
        return len(ids)

    async def dial(self, attempt_id: uuid.UUID) -> Call | None:
        """Rings the trainee for the attempt; no-op when the attempt is not a fresh
        call-intake one or another replica already dials it."""
        if attempt_id.hex in self.calls:
            return self.calls[attempt_id.hex]
        try:
            taken = await get_redis().set(
                f"telephony:dial:{attempt_id.hex}", "1", nx=True, ex=DIAL_LOCK_SECONDS
            )
        except Exception as exc:  # no Redis: still dial, a single replica is the usual case
            log.warning("dial lock unavailable", error=str(exc))
            taken = True
        if not taken:
            return None
        async with SessionLocal() as session:
            loaded = await load_attempt(session, attempt_id)
            if loaded is None or loaded.attempt.call_state != CALL_IDLE:
                return None
            if loaded.attempt.state not in ACTIVE_ATTEMPT_STATES:
                return None
            user = await session.get(User, loaded.attempt.student_id)
            if user is None:
                return None
            account, created = await sip.ensure_account(session, user)
            config = await telephony_settings.load(session)
            await session.commit()
            caller_name, caller_num = _caller_id(loaded.scenario)
            timeout = config.ring_timeout_seconds
        if created and self.sync_endpoints is not None:
            await self.sync_endpoints()
        call = self.call_class(
            attempt_id=attempt_id,
            session_id=loaded.attempt.session_id,
            student_id=loaded.attempt.student_id,
            login=account.login,
            channel_id=f"att-{attempt_id.hex}",
        )
        self.calls[call.key] = call
        self._by_channel[call.channel_id] = call
        try:
            await self.ari.create_channel(
                DIAL_ENDPOINT,
                call.channel_id,
                app_args=f"call,{call.key}",
                # Without an explicit format the Local channel picks slin192 and the audio
                # from the phone never reaches the bridge.
                formats=CHANNEL_FORMAT,
                variables={
                    VAR_LOGIN: account.login,
                    VAR_ATTEMPT: str(attempt_id),
                    VAR_RING_TIMEOUT: str(timeout),
                    # The dialplan puts it on the phone leg (a variable set on the Local
                    # channel after creation would not reach the other half).
                    VAR_CALLER_NAME: caller_name,
                    VAR_CALLER_NUM: caller_num,
                },
            )
            await self.ari.dial(call.channel_id, ring_seconds=timeout)
        except AriError as exc:
            log.warning("dial failed", attempt=str(attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return None
        log.info("dialling", attempt=str(attempt_id), login=account.login)
        async with SessionLocal() as session:
            attempt = await session.get(Attempt, attempt_id)
            events = await call_state.mark_ringing(session, attempt)
            await session.commit()
        await publish_events(events)
        return call

    async def hangup(self, attempt_id: uuid.UUID, reason: str = CALL_END_HANGUP) -> bool:
        call = self.call_for_attempt(attempt_id)
        if call is None:
            return False
        await self._end(call, reason)
        return True

    async def dial_service(
        self, attempt_id: uuid.UUID, call_id: str, service: str, service_title: str
    ) -> bool:
        """Rings the trainee's phones from the officer's number for a service call the
        dispatcher started from the card (issue #36). Returns False when nothing could be
        dialled (the API then answers in text)."""
        if call_id in self.calls:
            return True
        async with SessionLocal() as session:
            loaded = await load_card_attempt(session, attempt_id)
            if loaded is None or loaded.attempt.state not in ACTIVE_ATTEMPT_STATES:
                return False
            user = await session.get(User, loaded.attempt.student_id)
            if user is None:
                return False
            account, created = await sip.ensure_account(session, user)
            config = await telephony_settings.load(session)
            await session.commit()
            timeout = config.ring_timeout_seconds
        if created and self.sync_endpoints is not None:
            await self.sync_endpoints()
        call = Call(
            attempt_id=attempt_id,
            session_id=loaded.attempt.session_id,
            student_id=loaded.attempt.student_id,
            login=account.login,
            channel_id=f"svc-{call_id}",
            service_call_id=call_id,
            service=service,
            service_title=service_title,
        )
        self.calls[call.key] = call
        self._by_channel[call.channel_id] = call
        try:
            await self.ari.create_channel(
                SERVICE_DIAL_ENDPOINT,
                call.channel_id,
                app_args=f"service,{call.key}",
                formats=CHANNEL_FORMAT,
                variables={
                    VAR_LOGIN: account.login,
                    VAR_ATTEMPT: str(attempt_id),
                    VAR_SERVICE_CALL: call_id,
                    VAR_RING_TIMEOUT: str(timeout),
                    VAR_CALLER_NAME: service_title or service,
                    VAR_CALLER_NUM: service_number(service),
                },
            )
            await self.ari.dial(call.channel_id, ring_seconds=timeout)
        except AriError as exc:
            log.warning("service dial failed", call=call_id, error=str(exc))
            self.calls.pop(call.key, None)
            self._by_channel.pop(call.channel_id, None)
            return False
        log.info("dialling service", call=call_id, service=service, login=account.login)
        return True

    async def hangup_service_call(self, call_id: str, reason: str = CALL_END_HANGUP) -> bool:
        call = self.call_for_service_call(call_id)
        if call is None:
            return False
        await self._end(call, reason, already_stored=True)
        return True

    # ------------------------------------------------------------ events

    async def handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "StasisStart":
            await self._on_stasis_start(event)
        elif kind == "Dial":
            await self._on_dial(event)
        elif kind == "ChannelStateChange":
            channel = event.get("channel") or {}
            call = self._call_for_channel(channel.get("id"))
            if call and channel.get("state") == "Up":
                await self._on_answered(call)
        elif kind in ("StasisEnd", "ChannelDestroyed"):
            channel = event.get("channel") or {}
            call = self._call_for_channel(channel.get("id"))
            if call and channel.get("id") == call.channel_id:
                await self._end(call, _end_reason(call, event.get("cause")))
        elif kind == "PlaybackFinished":
            playback = event.get("playback") or {}
            for call in list(self.calls.values()):
                future = call.playbacks.pop(playback.get("id", ""), None)
                if future and not future.done():
                    future.set_result(None)
        elif kind == "RecordingFailed":
            recording = event.get("recording") or {}
            log.warning(
                "recording failed", name=recording.get("name"), cause=recording.get("cause")
            )

    async def _on_stasis_start(self, event: dict) -> None:
        """Helper channels are recognised by their ids (``snoop-<key>``, ``media-<key>``):
        ExternalMedia channels enter Stasis without application arguments."""
        channel = event.get("channel") or {}
        channel_id = channel.get("id") or ""
        role, _, key = channel_id.partition("-")
        if role not in ("snoop", "media"):
            return
        call = self.calls.get(key)
        if call is None:  # a leftover helper channel of a call we no longer track
            with contextlib.suppress(AriError):
                await self.ari.hangup(channel_id)
            return
        call.in_stasis.add(channel_id)
        if call.spy_bridge_id:
            with contextlib.suppress(AriError):
                await self.ari.add_channel(call.spy_bridge_id, channel_id)

    async def _on_dial(self, event: dict) -> None:
        peer = event.get("peer") or {}
        call = self._call_for_channel(peer.get("id"))
        if call is None:
            return
        status = event.get("dialstatus") or ""
        if status == "ANSWER":
            await self._on_answered(call)
        elif status in _DIAL_FAILED:
            await self._end(call, _DIAL_FAILED[status])

    # ------------------------------------------------------------ the conversation

    async def _on_answered(self, call: Call) -> None:
        async with call.lock:
            if call.answered or call.ended:
                return
            call.answered = True
        log.info("answered", attempt=str(call.attempt_id))
        async with SessionLocal() as session:
            config = await telephony_settings.load(session)
        # Media first (fast), so the recording and the spy start with the first second.
        try:
            call.bridge_id = f"br-{call.key}"
            await self.ari.create_bridge(call.bridge_id)
            await self.ari.add_channel(call.bridge_id, call.channel_id)
            if config.recording_enabled:
                call.recording_name = f"{RECORDINGS_SUBDIR}/{call.key}"
                await self.ari.record_bridge(call.bridge_id, call.recording_name, RECORDING_FORMAT)
            call.port = self.ports.take()
            call.receiver = await media.open_receiver("0.0.0.0", call.port)
            call.spy_bridge_id = f"spy-{call.key}"
            await self.ari.create_bridge(call.spy_bridge_id)
            call.media_id = f"media-{call.key}"
            call.snoop_id = f"snoop-{call.key}"
            await self.ari.external_media(
                call.media_id, self._media_host(), call.port, app_args=f"media,{call.key}"
            )
            await self.ari.snoop(call.channel_id, call.snoop_id, app_args=f"snoop,{call.key}")
        except (AriError, OSError, RuntimeError) as exc:
            log.warning("call setup failed", attempt=str(call.attempt_id), error=str(exc))
            await self._end(call, CALL_END_FAILED)
            return
        call.pipeline = asyncio.create_task(self._pipeline(call))
        if call.to_officer:
            await self._officer_answered(call)
            return
        # Then the state and the opening (its voice file is cached per scenario version).
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
            if loaded is None:
                await self._end(call, CALL_END_FAILED)
                return
            opening, events = await call_state.answer(
                session, loaded.attempt, loaded.ts, loaded.version, loaded.scenario, telephony=True
            )
            if call.recording_name:
                loaded.attempt.recording_path = f"{call.recording_name}.{RECORDING_FORMAT}"
            await session.commit()
        await publish_events(events)
        if opening.get("audio"):
            await self._play_reply(call, opening["audio"])

    async def _officer_answered(self, call: Call) -> None:
        """The trainee picked up the service call: the officer greets (issue #36)."""
        assert call.service_call_id is not None
        async with SessionLocal() as session:
            loaded = await load_card_attempt(session, call.attempt_id)
            if loaded is None:
                await self._end(call, CALL_END_FAILED)
                return
            greeting, events = await officer.answer(
                session,
                loaded.attempt,
                loaded.ts,
                loaded.version,
                loaded.scenario,
                call.service_call_id,
            )
            if call.recording_name:
                officer.set_recording(
                    loaded.attempt,
                    call.service_call_id,
                    f"{call.recording_name}.{RECORDING_FORMAT}",
                )
            await session.commit()
        await publish_events(events)
        turns = greeting.get("dialog") or []
        if turns and turns[0].get("audio"):
            await self._play_reply(call, turns[0]["audio"])

    async def _pipeline(self, call: Call) -> None:
        assert call.receiver is not None
        segmenter = media.Segmenter(media.build_vad(self.vad_model))
        try:
            while True:
                frame = await call.receiver.frames.get()
                if frame is None:
                    break
                for phrase in segmenter.feed(frame):
                    await self._on_phrase(call, phrase)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("call pipeline failed", attempt=str(call.attempt_id))
            await self._end(call, CALL_END_FAILED)

    async def _on_phrase(self, call: Call, pcm: bytes) -> None:
        if call.to_officer:
            await self._on_dispatcher_phrase(call, pcm)
            return
        heard_at = time.monotonic()
        wav = media.pcm_to_wav(pcm)
        async with SessionLocal() as session:
            loaded = await load_attempt(session, call.attempt_id)
            if loaded is None or loaded.attempt.call_state == CALL_ENDED:
                return
            address = loaded.scenario.reference_card.address
            hints = [h for h in (address.street, address.city, address.district) if h]
            transcript = await get_stt_provider().transcribe(wav, "phrase.wav", hints)
            if not transcript.available:
                log.warning("stt unavailable during a call", attempt=str(call.attempt_id))
                return
            if not transcript.text:
                return
            result = await dialog.say(
                session,
                loaded.attempt,
                loaded.ts,
                loaded.version,
                loaded.scenario,
                transcript.text,
                action_id=f"voice-{uuid.uuid4().hex[:12]}",
                heard=True,
            )
            await session.commit()
        await publish_events(result.events)
        log.info(
            "phrase",
            attempt=str(call.attempt_id),
            heard=transcript.text,
            reply=result.caller.get("text"),
            stt_ms=transcript.processing_ms,
            dialog_ms=result.latency_ms,
        )
        audio = result.caller.get("audio")
        if audio:
            call.reply_latencies.append(time.monotonic() - heard_at)
            await self._play_reply(call, audio)
        if result.call_ended:
            await self._end(call, CALL_END_CALLER_HANGUP, already_stored=True)

    async def _on_dispatcher_phrase(self, call: Call, pcm: bytes) -> None:
        """A phrase of the dispatcher on a service call: recognised, answered by the officer."""
        assert call.service_call_id is not None
        heard_at = time.monotonic()
        wav = media.pcm_to_wav(pcm)
        async with SessionLocal() as session:
            loaded = await load_card_attempt(session, call.attempt_id)
            if loaded is None:
                return
            record = officer.find_call(loaded.attempt, call.service_call_id)
            if record.get("ended_at"):
                return
            address = loaded.scenario.card.address
            hints = [h for h in (address.street, address.district, call.service_title) if h]
            transcript = await get_stt_provider().transcribe(wav, "phrase.wav", hints)
            if not transcript.available or not transcript.text:
                return
            result = await officer.say(
                session,
                loaded.attempt,
                loaded.ts,
                loaded.version,
                loaded.scenario,
                call.service_call_id,
                transcript.text,
                action_id=f"voice-{uuid.uuid4().hex[:12]}",
                heard=True,
            )
            await session.commit()
        await publish_events(result.events)
        log.info(
            "dispatcher phrase",
            call=call.service_call_id,
            heard=transcript.text,
            reply=result.officer.get("text"),
            stt_ms=transcript.processing_ms,
            dialog_ms=result.latency_ms,
        )
        audio = result.officer.get("audio")
        if audio:
            call.reply_latencies.append(time.monotonic() - heard_at)
            await self._play_reply(call, audio)

    async def _play_reply(self, call: Call, relative: str) -> None:
        source = dialog.audio_source_file(relative)
        if source is None:
            log.warning("reply audio missing", path=relative)
            return
        sound = await asyncio.to_thread(media.to_asterisk_sound, source)
        if sound is None:
            return
        await self._play(call, sound)

    async def _play(self, call: Call, sound: Path) -> None:
        """Plays a ``.sln16`` file of STORAGE_DIR into the call's bridge and waits for the end."""
        if call.ended or not call.bridge_id:
            return
        relative = sound.relative_to(dialog.storage_root()).with_suffix("")
        asterisk_path = Path(get_settings().asterisk_sounds_dir) / relative
        playback_id = f"pb-{call.key}-{uuid.uuid4().hex[:8]}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        call.playbacks[playback_id] = future
        try:
            await self.ari.play_bridge(
                call.bridge_id, f"sound:{asterisk_path.as_posix()}", playback_id
            )
            await asyncio.wait_for(future, timeout=PLAYBACK_TIMEOUT_SECONDS)
        except AriError as exc:
            call.playbacks.pop(playback_id, None)
            log.warning("playback failed", attempt=str(call.attempt_id), error=str(exc))
        except TimeoutError:
            call.playbacks.pop(playback_id, None)

    # ------------------------------------------------------------ teardown

    async def _end(self, call: Call, reason: str, *, already_stored: bool = False) -> None:
        async with call.lock:
            if call.ended:
                return
            call.ended = True
        log.info(
            "call ended",
            attempt=str(call.attempt_id),
            reason=reason,
            rtp_packets=call.receiver.packets if call.receiver else 0,
            replies=len(call.reply_latencies),
        )
        self.calls.pop(call.key, None)
        self._by_channel.pop(call.channel_id, None)
        if call.pipeline and call.pipeline is not asyncio.current_task():
            call.pipeline.cancel()
        if call.receiver:
            call.receiver.close()
        if call.port is not None:
            self.ports.give_back(call.port)
        for future in call.playbacks.values():
            if not future.done():
                future.cancel()
        if call.recording_name:
            with contextlib.suppress(AriError):
                await self.ari.stop_recording(call.recording_name)
        for channel_id in (call.channel_id, call.snoop_id, call.media_id):
            if channel_id:
                with contextlib.suppress(AriError):
                    await self.ari.hangup(channel_id)
        for bridge_id in (call.spy_bridge_id, call.bridge_id):
            if bridge_id:
                with contextlib.suppress(AriError):
                    await self.ari.destroy_bridge(bridge_id)
        if already_stored:
            return
        async with SessionLocal() as session:
            attempt = await session.get(Attempt, call.attempt_id)
            if attempt is None:
                return
            if call.to_officer:
                assert call.service_call_id is not None
                _, events = await officer.end(session, attempt, call.service_call_id, reason)
            else:
                events = await call_state.end(session, attempt, reason)
            await session.commit()
        await publish_events(events)

    async def shutdown(self) -> None:
        for call in list(self.calls.values()):
            await self._end(call, CALL_END_FAILED)


# ISDN causes (ChannelDestroyed) that mean the phone could not be reached at all.
_CAUSE_UNREACHABLE = {1, 3, 20, 27, 34, 38, 41, 42, 44, 47, 58}


def _end_reason(call: Call, cause: int | None) -> str:
    if call.answered:
        return CALL_END_HANGUP
    if cause in _CAUSE_UNREACHABLE:
        return CALL_END_FAILED
    return CALL_END_NO_ANSWER


def _caller_id(scenario: CallIntakeScenario) -> tuple[str, str]:
    """Name and number the softphone shows: the caller's phone from the reference card,
    else 112."""
    phone = scenario.reference_card.caller.phone or scenario.caller.facts.get("callback_phone")
    digits = "".join(ch for ch in (phone or "") if ch.isdigit()) or DEFAULT_CALLER_NUMBER
    name = scenario.reference_card.caller.name or "Заявитель"
    return name, digits
