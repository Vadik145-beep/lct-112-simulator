"""Life cycle of telephony inside the backend process: the ARI event loop, the Redis
subscription that turns ``attempt.issued`` into a call, the endpoints file for Asterisk.

``TELEPHONY_ENABLED=false`` (the default) starts nothing: the call panel then works through
the browser microphone and ``/attempts/{id}/utterance``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import redis.asyncio as aioredis

from app.config import get_settings
from app.db import SessionLocal
from app.events import get_redis
from app.logging import get_logger
from app.telephony import media, sip
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriClient, AriError
from app.telephony.calls import CallManager
from app.telephony.cloud import CloudCallManager
from app.telephony.vapi import VapiClient

log = get_logger(__name__)

EVENTS_PATTERN = "session-events:*"
PJSIP_MODULE = "res_pjsip.so"
REDIS_RETRY_SECONDS = 5


class TelephonyService:
    def __init__(self) -> None:
        s = get_settings()
        self.ari = AriClient(s.ari_url, s.ari_user, s.ari_password, s.ari_app)
        self.vapi: VapiClient | None = None
        self.cloud = s.cloud_voice_enabled
        if self.cloud:
            # Lessons in the cloud mode get Vapi as the caller (plan/track-c-vapi.md), the
            # rest the local pipeline; the SIP number is set up in start(), so a missing key
            # only sends cloud lessons to the local pipeline, not the service down.
            if s.vapi_api_key:
                self.vapi = VapiClient(s.vapi_api_url, s.vapi_api_key)
            self.calls: CallManager = CloudCallManager(self.ari, self.vapi)
        else:
            self.calls = CallManager(self.ari)
        self.calls.sync_endpoints = self.sync_endpoints
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    @property
    def connected(self) -> bool:
        return self.ari.connected

    async def start(self) -> None:
        await self.sync_endpoints()
        if isinstance(self.calls, CloudCallManager):
            await self.calls.setup()
        self._tasks = [
            asyncio.create_task(self.ari.run_events(self.calls.handle_event, self._stop)),
            asyncio.create_task(self._subscribe_events()),
            asyncio.create_task(self._dial_pending_when_connected()),
            asyncio.create_task(self.warm_openings()),
        ]
        log.info("telephony started", ari=self.ari.base_url)

    async def stop(self) -> None:
        self._stop.set()
        await self.calls.shutdown()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.ari.aclose()
        if self.vapi is not None:
            await self.vapi.aclose()

    async def sync_endpoints(self) -> None:
        """Rewrites the PJSIP endpoints file from the users table and reloads PJSIP."""
        async with SessionLocal() as session:
            accounts = await sip.all_accounts(session)
            config = await telephony_settings.load(session)
        path = sip.write_endpoints(accounts, config)
        if path is None:
            return
        try:
            await self.ari.reload_module(PJSIP_MODULE)
        except AriError as exc:
            log.warning("pjsip reload failed", error=str(exc))

    async def warm_openings(self) -> None:
        """Voices the opening of every approved call-intake scenario ahead of the first call:
        Piper loads a voice in seconds, and the first call must not wait for it."""
        from sqlalchemy import select

        from app.dialog import service as dialog
        from app.domain.evaluation.schemas import CallIntakeScenario
        from app.models import MODE_CALL_INTAKE, SCENARIO_APPROVED, Scenario, ScenarioVersion

        async with SessionLocal() as session:
            rows = await session.execute(
                select(ScenarioVersion)
                .join(Scenario, Scenario.id == ScenarioVersion.scenario_id)
                .where(
                    Scenario.kind == MODE_CALL_INTAKE,
                    Scenario.status == SCENARIO_APPROVED,
                    ScenarioVersion.version == Scenario.current_version,
                )
            )
            versions = [row[0] for row in rows]
        done = 0
        for version in versions:
            if self._stop.is_set():
                return
            try:
                scenario = CallIntakeScenario.model_validate(version.body)
                audio = await dialog.opening_audio(version, scenario)
                source = dialog.audio_source_file(audio) if audio else None
                if source is not None:
                    await asyncio.to_thread(media.to_asterisk_sound, source)
                    done += 1
            except Exception as exc:  # a broken scenario must not stop the rest
                log.warning(
                    "opening warm-up failed", scenario=str(version.scenario_id), error=str(exc)
                )
        log.info("openings warmed", count=done)

    async def _dial_pending_when_connected(self) -> None:
        for _ in range(30):
            if self._stop.is_set():
                return
            if self.ari.connected:
                count = await self.calls.dial_pending()
                if count:
                    log.info("pending calls dialled", count=count)
                return
            await asyncio.sleep(1)

    async def _subscribe_events(self) -> None:
        """``attempt.issued`` of a call-intake attempt → dial the trainee."""
        while not self._stop.is_set():
            try:
                pubsub = get_redis().pubsub()
                await pubsub.psubscribe(EVENTS_PATTERN)
                async for message in pubsub.listen():
                    if self._stop.is_set():
                        break
                    if message.get("type") != "pmessage":
                        continue
                    await self._on_session_event(message.get("data"))
            except asyncio.CancelledError:
                raise
            except (OSError, aioredis.RedisError) as exc:
                log.warning("telephony event subscription lost", error=str(exc))
                await asyncio.sleep(REDIS_RETRY_SECONDS)

    async def _on_session_event(self, raw: str | bytes | None) -> None:
        if not raw:
            return
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if event.get("type") != "attempt.issued":
            return
        attempt_id = (event.get("payload") or {}).get("attempt_id")
        if not attempt_id:
            return
        try:
            await self.calls.dial(uuid.UUID(str(attempt_id)))
        except Exception:
            log.exception("dial from event failed", attempt=attempt_id)


_service: TelephonyService | None = None


def get_service() -> TelephonyService | None:
    return _service


def telephony_active() -> bool:
    """True when calls go through Asterisk right now (enabled and ARI is connected)."""
    return _service is not None and _service.connected


def for_session(ts: object | None) -> bool:
    """The calls of this lesson go through Asterisk: telephony is up and the lesson has the
    «звонок на телефон» box (``phone_calls``). Without the box the lesson talks in the
    browser, as on a stand without telephony — its call panel stays the same whatever the
    stand runs (решение пользователя 27.09.2026)."""
    return telephony_active() and bool(getattr(ts, "phone_calls", False))


def cloud_active() -> bool:
    """True when SIP calls of cloud lessons can reach Vapi (plan/track-c-vapi.md)."""
    return telephony_active() and _service is not None and _service.cloud


async def start() -> None:
    global _service
    if not get_settings().telephony_enabled or _service is not None:
        return
    _service = TelephonyService()
    await _service.start()


async def stop() -> None:
    global _service
    if _service is None:
        return
    await _service.stop()
    _service = None
