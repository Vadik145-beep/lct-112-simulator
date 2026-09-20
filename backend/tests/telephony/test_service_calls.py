"""A dispatcher's call to a service officer over Asterisk (issue #36): the trainee's phone is
rung from the officer's number, the officer greets, the dispatcher's phrase is recognised and
answered, the recording and the events go to the call's own record, and hanging up ends it."""

from __future__ import annotations

import struct
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.dialog import officer
from app.dialog import service as dialog
from app.models import MODE_CARD_RESPONSE, Attempt
from app.telephony import calls as calls_module
from app.telephony import media
from app.telephony.calls import CallManager
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.telephony.fake_ari import FakeAri
from tests.telephony.test_calls import (
    FakeStt,
    _added_to_spy,
    events_of,
    load,
    send_to,
    tone_rtp_packets,
    wait_until,
)

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

CARD = "card_moek_1_net_otopleniya"


async def start_call(attempt_id: uuid.UUID) -> str:
    """The record the API creates before dialling (``officer.start`` with telephony)."""
    async with SessionLocal() as session:
        loaded = await calls_module.load_card_attempt(session, attempt_id)
        assert loaded is not None
        call, _ = await officer.start(
            session,
            loaded.attempt,
            loaded.ts,
            loaded.version,
            loaded.scenario,
            "moek",
            "МОЭК",
            telephony=True,
        )
        await session.commit()
        return call["id"]


def service_call_of(attempt: Attempt, call_id: str) -> dict:
    return next(c for c in attempt.service_calls if c["id"] == call_id)


async def test_service_call_rings_from_the_officer_and_runs_the_dialog(
    manager: CallManager, fake_ari: FakeAri, monkeypatch: pytest.MonkeyPatch
):
    storage = Path(get_settings().storage_dir)
    folder = storage / "tts" / "fake-officer" / "v1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "greeting.wav").write_bytes(media.pcm_to_wav(bytes(3200)))
    (folder / "greeting.sln16").write_bytes(bytes(3200))

    async def fake_reply_audio(attempt, version, scenario, reply, stem=None):
        return "tts/fake-officer/v1/greeting.wav"

    monkeypatch.setattr(dialog, "reply_audio", fake_reply_audio)
    monkeypatch.setattr(media, "to_asterisk_sound", lambda source: source.with_suffix(".sln16"))
    stt = FakeStt("Передаю карточку: улица Молостовых, дом 10, корпус 1, нет отопления")
    monkeypatch.setattr(calls_module, "get_stt_provider", lambda: stt)

    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    call = manager.call_for_service_call(call_id)
    assert call is not None and call.to_officer
    # The Local channel goes to the service context with the officer as the caller id.
    originate = fake_ari.calls("POST", "/channels/create")[-1]
    assert originate.params["endpoint"] == "Local/s@service-out/n"
    variables = (originate.body or {}).get("variables") or {}
    assert variables["__SERVICE_CALL_ID"] == call_id
    assert variables["__CALLER_NAME"] == "МОЭК"
    assert variables["__CALLER_NUM"].isdigit()
    assert manager.call_for_attempt(attempt_id) is None  # not a 112 call of the attempt

    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    await fake_ari.wait_for("POST", "/record")
    await fake_ari.wait_for("POST", "/snoop")
    await wait_until(lambda: _added_to_spy(fake_ari, call))
    play = await fake_ari.wait_for("POST", "/play")
    assert play.params["media"].endswith("tts/fake-officer/v1/greeting")
    await wait_until(lambda: _answered(attempt_id, call_id))
    record = service_call_of(await load(attempt_id), call_id)
    assert record["answered"] is True
    assert record["recording_path"] == f"recordings/{call_id}.wav"
    assert record["dialog"][0]["role"] == "caller"
    assert "слушаю" in record["dialog"][0]["text"]
    assert "service_call.answered" in await events_of(attempt_id)

    # The dispatcher speaks: recognised, the officer answers, the facts are counted.
    silence = [
        struct.pack("!BBHII", 0x80, 118, 5000 + i, 0, 0x1234) + bytes(640) for i in range(40)
    ]
    await send_to(call.port, silence + tone_rtp_packets(0.8) + silence)
    await wait_until(lambda: _has_dispatcher_turn(attempt_id, call_id), seconds=8)
    assert stt.calls == 1
    record = service_call_of(await load(attempt_id), call_id)
    dispatcher = [t for t in record["dialog"] if t["role"] == "operator"]
    assert dispatcher[0]["heard"] is True
    assert set(record["facts_passed"]) >= {"address", "incident_type"}
    assert "service_call.turn" in await events_of(attempt_id)
    attempt = await load(attempt_id)
    assert attempt.dialog == []  # the 112 dialog of the attempt is untouched

    # The trainee hangs up: the record is closed, the legs are torn down.
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id, call_id))
    record = service_call_of(await load(attempt_id), call_id)
    assert record["end_reason"] == "hangup"
    assert fake_ari.calls("POST", "/recordings/live/")
    assert "service_call.ended" in await events_of(attempt_id)
    assert manager.call_for_service_call(call_id) is None


async def test_hangup_from_the_api_ends_the_leg(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК")
    channel = manager.call_for_service_call(call_id).channel_id
    assert await manager.hangup_service_call(call_id) is True
    await fake_ari.wait_for("DELETE", f"/channels/{channel}")
    assert manager.call_for_service_call(call_id) is None
    # already_stored: the API closed the record itself, the manager leaves it alone.
    record = service_call_of(await load(attempt_id), call_id)
    assert record["ended_at"] is None


async def test_dial_service_refuses_a_closed_card(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        attempt.state = "finished"
        await session.commit()
    assert await manager.dial_service(attempt_id, uuid.uuid4().hex, "moek", "МОЭК") is False
    assert not fake_ari.calls("POST", "/channels/create")


async def _answered(attempt_id: uuid.UUID, call_id: str) -> bool:
    return service_call_of(await load(attempt_id), call_id)["answered"]


async def _has_dispatcher_turn(attempt_id: uuid.UUID, call_id: str) -> bool:
    record = service_call_of(await load(attempt_id), call_id)
    return any(t["role"] == "operator" for t in record["dialog"])


async def _ended(attempt_id: uuid.UUID, call_id: str) -> bool:
    return service_call_of(await load(attempt_id), call_id)["ended_at"] is not None
