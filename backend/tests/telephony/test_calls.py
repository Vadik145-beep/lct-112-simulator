"""The ARI client and the call manager against a stand-in ARI server (plan wave 6): originate,
the events of the call, playback of the opening, the operator's phrase through the media
socket, teardown. Speech recognition and the voice are replaced by fakes; the dialog runs
without a model (buttons mode)."""

from __future__ import annotations

import asyncio
import math
import struct
import uuid
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import service as dialog
from app.models import (
    CALL_ANSWERED,
    CALL_END_CALLER_HANGUP,
    CALL_END_FAILED,
    CALL_END_HANGUP,
    CALL_ENDED,
    CALL_RINGING,
    Attempt,
    SessionEvent,
)
from app.providers.stt import Transcript
from app.telephony import calls as calls_module
from app.telephony import media
from app.telephony.ari import AriClient
from app.telephony.calls import CallManager
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.telephony.fake_ari import FakeAri

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

DROPS_CALL = "call_2-2_skandal_sryv_zvonka"


class FakeStt:
    method = "fake"

    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def transcribe(self, audio: bytes, filename: str = "audio.wav", hints=()) -> Transcript:
        self.calls += 1
        return Transcript(self.text, "whisper", processing_ms=1)


async def load(attempt_id: uuid.UUID) -> Attempt:
    async with SessionLocal() as session:
        return await session.get(Attempt, attempt_id)


async def events_of(attempt_id: uuid.UUID) -> list[str]:
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        rows = await session.scalars(
            select(SessionEvent)
            .where(SessionEvent.session_id == attempt.session_id)
            .order_by(SessionEvent.seq)
        )
        return [e.type for e in rows]


async def wait_until(predicate, seconds: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not await predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("condition not met")
        await asyncio.sleep(0.05)


def tone_rtp_packets(seconds: float) -> list[bytes]:
    n = int(seconds * media.SAMPLE_RATE)
    samples = (12000 * np.sin(2 * math.pi * 300 * np.arange(n) / media.SAMPLE_RATE)).astype("<i2")
    pcm = samples.tobytes()
    packets = []
    for i, frame in enumerate(media.iter_frames(pcm)):
        header = struct.pack("!BBHII", 0x80, 118, i + 1, i * 320, 0x1234)
        packets.append(header + media.pcm_to_rtp_payload(frame))
    return packets


async def send_to(port: int, packets: list[bytes]) -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, remote_addr=("127.0.0.1", port)
    )
    try:
        for packet in packets:
            transport.sendto(packet)
            await asyncio.sleep(0.002)
    finally:
        transport.close()


async def test_ari_client_connects_and_pings(fake_ari: FakeAri):
    ari = AriClient(f"http://127.0.0.1:{fake_ari.port}/ari", "trainer", "trainer", "trainer")
    assert await ari.ping()
    assert ari.events_url().startswith(f"ws://127.0.0.1:{fake_ari.port}/ari/events?")
    assert "api_key=trainer%3Atrainer" in ari.events_url()
    await ari.aclose()


async def test_dial_rings_the_trainee(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None
    create = await fake_ari.wait_for("POST", "/channels/create")
    assert create.params["endpoint"] == "Local/s@trainer-out/n"
    assert create.params["formats"] == "slin16"
    assert create.body["variables"]["__STU_LOGIN"] == "student1"
    assert create.body["variables"]["__ATTEMPT_ID"] == str(attempt_id)
    assert create.body["variables"]["__CALLER_NUM"].isdigit()
    assert create.body["variables"]["__CALLER_NAME"]
    dial = await fake_ari.wait_for("POST", "/dial")
    assert dial.path.endswith(f"/channels/{call.channel_id}/dial")
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_RINGING
    assert "call.ringing" in await events_of(attempt_id)
    # The second dial of the same attempt is a no-op.
    assert await manager.dial(attempt_id) is call


async def test_no_answer_and_unreachable(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 3, "channel": {"id": call.channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id))
    attempt = await load(attempt_id)
    assert attempt.call_end_reason == CALL_END_FAILED
    assert manager.call_for_attempt(attempt_id) is None

    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    await fake_ari.emit({"type": "StasisEnd", "channel": {"id": call.channel_id}})
    await wait_until(lambda: _ended(attempt_id))
    assert (await load(attempt_id)).call_end_reason == "no_answer"


async def _ended(attempt_id: uuid.UUID) -> bool:
    return (await load(attempt_id)).call_state == CALL_ENDED


async def test_answered_call_plays_opening_hears_phrase_and_ends(
    manager: CallManager, fake_ari: FakeAri, monkeypatch: pytest.MonkeyPatch
):
    storage = Path(get_settings().storage_dir)
    folder = storage / "tts" / "fake-scenario" / "v1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "opening.wav").write_bytes(media.pcm_to_wav(bytes(3200)))
    (folder / "opening.sln16").write_bytes(bytes(3200))

    async def fake_opening_audio(version, scenario):
        return "tts/fake-scenario/v1/opening.wav"

    monkeypatch.setattr(dialog, "opening_audio", fake_opening_audio)
    monkeypatch.setattr(media, "to_asterisk_sound", lambda source: source.with_suffix(".sln16"))
    stt = FakeStt("Диктуйте адрес")
    monkeypatch.setattr(calls_module, "get_stt_provider", lambda: stt)

    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )

    # Media set-up: bridge, recording, spy bridge with snoop and ExternalMedia.
    await fake_ari.wait_for("POST", "/record")
    external = await fake_ari.wait_for("POST", "/channels/externalMedia")
    assert external.params["external_host"].startswith("127.0.0.1:130")
    assert external.params["format"] == "slin16"
    snoop = await fake_ari.wait_for("POST", "/snoop")
    assert snoop.params["spy"] == "in"
    await wait_until(lambda: _added_to_spy(fake_ari, call))
    # The opening is played into the bridge as an Asterisk sound file.
    play = await fake_ari.wait_for("POST", "/play")
    assert play.params["media"] == (
        f"sound:{get_settings().asterisk_sounds_dir}/tts/fake-scenario/v1/opening"
    )
    await wait_until(lambda: _answered(attempt_id))
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_ANSWERED
    assert attempt.answered_at is not None
    assert attempt.recording_path == f"recordings/{attempt_id.hex}.wav"
    assert attempt.dialog[0]["method"] == "opening"
    assert attempt.dialog[0]["audio"] == "tts/fake-scenario/v1/opening.wav"
    assert "call.answered" in await events_of(attempt_id)

    # The operator speaks: a tone with silence around it arrives as RTP on the call's port.
    silence = [
        struct.pack("!BBHII", 0x80, 118, 5000 + i, 0, 0x1234) + bytes(640) for i in range(40)
    ]
    await send_to(call.port, silence + tone_rtp_packets(0.8) + silence)
    await wait_until(lambda: _has_operator_turn(attempt_id), seconds=8)
    assert stt.calls == 1
    attempt = await load(attempt_id)
    operator = [t for t in attempt.dialog if t["role"] == "operator"]
    assert operator[0]["text"] == "Диктуйте адрес"
    assert operator[0]["heard"] is True
    assert "address" in attempt.dialog[-1]["topics"]
    assert "dialog.turn" in await events_of(attempt_id)

    # The trainee hangs up.
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id))
    attempt = await load(attempt_id)
    assert attempt.call_end_reason == CALL_END_HANGUP
    assert fake_ari.calls("POST", "/recordings/live/")
    assert len(fake_ari.calls("DELETE", "/bridges/")) == 2
    assert [r.path for r in fake_ari.calls("DELETE", "/channels/")]
    assert "call.ended" in await events_of(attempt_id)
    assert manager.call_for_attempt(attempt_id) is None


async def _added_to_spy(fake_ari: FakeAri, call) -> bool:
    added = {
        r.params.get("channel")
        for r in fake_ari.calls("POST", f"/bridges/{call.spy_bridge_id}/addChannel")
    }
    return {call.snoop_id, call.media_id} <= added


async def _answered(attempt_id: uuid.UUID) -> bool:
    return (await load(attempt_id)).call_state == CALL_ANSWERED


async def _has_operator_turn(attempt_id: uuid.UUID) -> bool:
    attempt = await load(attempt_id)
    return any(t["role"] == "operator" for t in attempt.dialog)


async def test_caller_drops_the_call_after_second_question(
    manager: CallManager, fake_ari: FakeAri, monkeypatch: pytest.MonkeyPatch
):
    stt = FakeStt("Что случилось?")
    monkeypatch.setattr(calls_module, "get_stt_provider", lambda: stt)
    attempt_id = await make_attempt(DROPS_CALL)
    call = await manager.dial(attempt_id)
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _answered(attempt_id))
    await wait_until(lambda: _added_to_spy(fake_ari, call))
    silence = [
        struct.pack("!BBHII", 0x80, 118, 7000 + i, 0, 0x1234) + bytes(640) for i in range(40)
    ]
    await send_to(call.port, silence + tone_rtp_packets(0.8) + silence)
    await wait_until(lambda: _has_operator_turn(attempt_id), seconds=8)
    await send_to(call.port, silence + tone_rtp_packets(0.8) + silence)
    await wait_until(lambda: _ended(attempt_id), seconds=8)
    attempt = await load(attempt_id)
    assert attempt.call_end_reason == CALL_END_CALLER_HANGUP
    assert stt.calls == 2

    # Asterisk was asked to hang up the trainee's channel — after the farewell reply has been
    # played, so a moment later than the stored «ended» state the wait above saw.
    async def hung_up() -> bool:
        return any(
            r.path.endswith(f"/channels/{call.channel_id}")
            for r in fake_ari.calls("DELETE", "/channels/")
        )

    await wait_until(hung_up)


async def test_manager_hangup_deletes_channel(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    assert await manager.hangup(attempt_id)
    assert any(
        r.path.endswith(f"/channels/{call.channel_id}")
        for r in fake_ari.calls("DELETE", "/channels/")
    )
    assert (await load(attempt_id)).call_end_reason == CALL_END_HANGUP
    assert not await manager.hangup(attempt_id)
