"""MultiFon (docs/MULTIFON.md): the trainee's own phone rings through the MultiFon trunk next to
the softphone, and a call to the MultiFon number from that phone takes over the call ringing
for him — a 112 call or any call of the card. In a cloud lesson the calls of the card go to
Vapi over SIP as the 112 call does: the officer, the squad leader or the caller rung back."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    CALL_ANSWERED,
    CALL_END_HANGUP,
    MODE_CARD_RESPONSE,
    Attempt,
    TrainingSession,
    User,
)
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriClient
from app.telephony.calls import RINGBACK_MEDIA, VAR_MOBILE, CallManager
from app.telephony.cloud import VAPI_DIAL_ENDPOINT, CloudCall, CloudCallManager
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import make_attempt
from tests.telephony.fake_ari import FakeAri
from tests.telephony.test_calls import _ended, load, wait_until
from tests.telephony.test_cloud import SIP_USER, message
from tests.telephony.test_service_calls import CARD, service_call_of, start_call

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

PHONE = "79220000001"


@pytest.fixture
async def cloud_manager(
    fake_ari: FakeAri, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[CloudCallManager]:
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_public_url", "https://stand.example.org")
    ari = AriClient(f"http://127.0.0.1:{fake_ari.port}/ari", "trainer", "trainer", "trainer")
    manager = CloudCallManager(ari, vapi=None)
    manager.sip_user = SIP_USER
    stop = asyncio.Event()
    task = asyncio.create_task(ari.run_events(manager.handle_event, stop))
    await fake_ari.wait_for_client()
    yield manager
    stop.set()
    await manager.shutdown()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await ari.aclose()


@pytest.fixture
def trainee_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "telephony_trainee_phones", "Student1=8 (922) 000-00-01")


async def phone_attempt(*args, **kwargs) -> uuid.UUID:
    """An attempt of a lesson with calls to the phone (the teacher's checkbox)."""
    attempt_id = await make_attempt(*args, **kwargs)
    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        ts = await session.get(TrainingSession, attempt.session_id)
        ts.phone_calls = True
        await session.commit()
    return attempt_id


async def set_own_phone(login: str, phone: str | None) -> None:
    from sqlalchemy import select

    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.login == login))
        user.phone = phone
        await session.commit()


# ---------------------------------------------------------------- phones


def test_phones_are_normalized_for_multifon():
    normalize = telephony_settings.normalize_phone
    assert normalize("8 900 123-45-67") == "79001234567"
    assert normalize("+7 (900) 123-45-67") == "79001234567"
    assert normalize("9001234567") == "79001234567"
    assert normalize("112") == ""
    assert normalize("") == ""
    assert telephony_settings.same_phone("+79001234567", "89001234567")
    assert not telephony_settings.same_phone("", "")
    assert telephony_settings.parse_phones("a=89220000001, b=oops ,=1") == {"a": "79220000001"}


def test_administrator_phones_are_checked():
    cleaned = telephony_settings.clean_phones({"Student1": "8 922 000 00 01", "student2": ""})
    assert cleaned == {"student1": "79220000001"}
    with pytest.raises(ValueError, match="11 цифр"):
        telephony_settings.clean_phones({"student1": "12345"})


# ---------------------------------------------------------------- ringing and taking over


async def test_dial_rings_the_trainee_phone_too(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    attempt_id = await phone_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None and call.phone == PHONE
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == PHONE


async def test_without_a_phone_the_mobile_is_empty(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await phone_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None and call.phone == ""
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == ""


async def test_call_from_the_trainee_phone_takes_over_the_ringing_call(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    attempt_id = await phone_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None
    ringing = call.channel_id
    await fake_ari.emit(
        {
            "type": "StasisStart",
            "args": ["inbound", "+79220000001"],
            "channel": {"id": "mf-in-1", "caller": {"number": "+79220000001"}},
        }
    )
    await fake_ari.wait_for("POST", "/channels/mf-in-1/answer")
    # The ringing legs are dropped, the incoming channel is the trainee's side now.
    await fake_ari.wait_for("DELETE", f"/channels/{ringing}")
    await wait_until(lambda: _answered(attempt_id))
    assert call.channel_id == "mf-in-1"
    added = [r.params.get("channel") for r in fake_ari.calls("POST", "/addChannel")]
    assert "mf-in-1" in added
    # The ringing channel going away later does not end the call.
    await fake_ari.emit({"type": "ChannelDestroyed", "cause": 16, "channel": {"id": ringing}})
    assert not call.ended
    await fake_ari.emit({"type": "ChannelDestroyed", "cause": 16, "channel": {"id": "mf-in-1"}})
    await wait_until(lambda: _ended(attempt_id))
    assert (await load(attempt_id)).call_end_reason == CALL_END_HANGUP


async def test_call_from_an_unknown_phone_is_refused(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    attempt_id = await phone_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None
    await fake_ari.emit(
        {"type": "StasisStart", "args": ["inbound", "89990000000"], "channel": {"id": "mf-in-2"}}
    )
    refused = await fake_ari.wait_for("DELETE", "/channels/mf-in-2")
    assert refused.params.get("reason") == "busy"
    assert not call.answered
    assert not fake_ari.calls("POST", "/channels/mf-in-2/answer")


async def test_call_from_the_phone_picks_up_a_service_call(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    attempt_id = await phone_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == PHONE
    await fake_ari.emit(
        {"type": "StasisStart", "args": ["inbound", "89220000001"], "channel": {"id": "mf-in-3"}}
    )
    await fake_ari.wait_for("POST", "/channels/mf-in-3/answer")
    await wait_until(lambda: _service_answered(attempt_id, call_id))


async def test_lesson_without_phone_calls_does_not_ring_the_phone(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    """The number alone is not enough: the teacher turns the phone on per lesson, and a
    lesson without the box is not dialled at all — it talks in the browser (27.09.2026)."""
    before = len(fake_ari.calls("POST", "/channels/create"))
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    assert call is None
    assert len(fake_ari.calls("POST", "/channels/create")) == before


async def test_own_number_comes_before_the_administrator_table(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    await set_own_phone("student1", "79220000009")
    try:
        attempt_id = await phone_attempt()
        call = await manager.dial(attempt_id)
        assert call is not None and call.phone == "79220000009"
        # The trainee's own number rings next to the browser, as before.
        create = fake_ari.calls("POST", "/channels/create")[-1]
        assert create.body["variables"]["__PHONE_ONLY"] == ""
    finally:
        await set_own_phone("student1", None)


async def test_teacher_number_of_the_lesson_comes_first(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    """The teacher entered the number of the live call: it rings, not the trainee's own."""
    await set_own_phone("student1", "79220000009")
    try:
        attempt_id = await phone_attempt()
        async with SessionLocal() as session:
            attempt = await session.get(Attempt, attempt_id)
            ts = await session.get(TrainingSession, attempt.session_id)
            ts.phone = "79220000077"
            await session.commit()
        call = await manager.dial(attempt_id)
        assert call is not None and call.phone == "79220000077"
        create = fake_ari.calls("POST", "/channels/create")[-1]
        assert create.body["variables"][VAR_MOBILE] == "79220000077"
        # Only the phone rings: answering in the browser would end the call.
        assert create.body["variables"]["__PHONE_ONLY"] == "1"
        from app.dialog.router import call_out

        async with SessionLocal() as session:
            attempt = await session.get(Attempt, attempt_id)
            ts = await session.get(TrainingSession, attempt.session_id)
            assert call_out(attempt, ts).phone == "79220000077"
    finally:
        await set_own_phone("student1", None)


async def test_dispatcher_call_on_the_phone_rings_back_before_the_officer(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None, monkeypatch: pytest.MonkeyPatch
):
    """«Позвонить» on the phone: after the pick-up the dispatcher hears the ring-back, then
    the officer greets. A squad's report (incoming) has no ring-back."""
    from app.telephony import calls as calls_module

    monkeypatch.setattr(calls_module, "RINGBACK_SECONDS", 0.2)
    attempt_id = await phone_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    call = manager.call_for_service_call(call_id)
    assert call is not None and call.ringback
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    first = await fake_ari.wait_for("POST", "/play")
    assert first.params["media"] == RINGBACK_MEDIA
    await fake_ari.wait_for("DELETE", f"/playbacks/rb-{call.key}")
    await wait_until(lambda: _service_answered(attempt_id, call_id))

    # «Завершить» in the card: the record is closed, then the leg is hung up.
    from app.dialog import officer

    async with SessionLocal() as session:
        attempt = await session.get(Attempt, attempt_id)
        await officer.end(session, attempt, call_id, officer.END_HANGUP)
        await session.commit()
    assert await manager.hangup_service_call(call_id)
    report_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, report_id, "moek", "МОЭК", kind="report")
    report = manager.call_for_service_call(report_id)
    assert report is not None and report.phone == PHONE and not report.ringback


# ---------------------------------------------------------------- calls of the card in the cloud


async def cloud_service_call(manager: CloudCallManager, fake_ari: FakeAri):
    attempt_id = await phone_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="cloud")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    call = manager.call_for_service_call(call_id)
    assert isinstance(call, CloudCall)
    assert manager.call_for_token(call.token) is call
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )

    async def vapi_leg_dialled() -> bool:
        return any(
            r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
            for r in fake_ari.calls("POST", "/channels/create")
        )

    await wait_until(vapi_leg_dialled)
    return attempt_id, call_id, call


async def test_service_call_of_a_cloud_lesson_goes_to_vapi(
    cloud_manager: CloudCallManager,
    fake_ari: FakeAri,
):
    manager = cloud_manager
    attempt_id, call_id, call = await cloud_service_call(manager, fake_ari)
    # No spy and no greeting of ours: the officer is Vapi.
    await fake_ari.wait_for("POST", "/record")
    assert not fake_ari.calls("POST", "/snoop")
    assert not fake_ari.calls("POST", "/play")

    # Vapi asks for the assistant: the officer of the service, not the 112 caller.
    reply = await manager.webhook(message("assistant-request", call))
    assistant = reply["assistant"]
    assert "дежурный службы" in assistant["model"]["messages"][0]["content"].lower()

    # Vapi picks up: its leg is bridged, the record is answered without our greeting.
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _service_answered(attempt_id, call_id))
    record = service_call_of(await load(attempt_id), call_id)
    assert record["dialog"] == []
    assert record["recording_path"] == f"recordings/{call_id}.wav"

    # Phrases go to the call's own transcript.
    await manager.webhook(message("transcript", call, role="assistant", transcript="МОЭК, слушаю."))
    await manager.webhook(
        message("transcript", call, role="user", transcript="Нет отопления на Молостовых, 10")
    )
    record = service_call_of(await load(attempt_id), call_id)
    assert [t["role"] for t in record["dialog"]] == ["caller", "operator"]
    assert (await load(attempt_id)).dialog in (None, [])

    # Vapi ends the call: the record is closed.
    await manager.webhook(
        message("status-update", call, status="ended", endedReason="assistant-ended-call")
    )
    await wait_until(lambda: _service_ended(attempt_id, call_id))
    assert manager.call_for_token(call.token) is None


async def test_service_call_falls_back_to_the_local_officer(
    cloud_manager: CloudCallManager,
    fake_ari: FakeAri,
    monkeypatch: pytest.MonkeyPatch,
):
    from pathlib import Path

    from app.dialog import service as dialog
    from app.telephony import media

    folder = Path(get_settings().storage_dir) / "tts" / "fake-officer" / "v1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "greeting.wav").write_bytes(media.pcm_to_wav(bytes(3200)))
    (folder / "greeting.sln16").write_bytes(bytes(3200))

    async def fake_reply_audio(attempt, version, scenario, reply, stem=None):
        return "tts/fake-officer/v1/greeting.wav"

    monkeypatch.setattr(dialog, "reply_audio", fake_reply_audio)
    monkeypatch.setattr(media, "to_asterisk_sound", lambda source: source.with_suffix(".sln16"))

    manager = cloud_manager
    attempt_id, call_id, call = await cloud_service_call(manager, fake_ari)
    await fake_ari.emit(
        {
            "type": "Dial",
            "dialstatus": "CONGESTION",
            "peer": {"id": call.vapi_channel_id},
            "caller": {"id": call.channel_id},
        }
    )
    # The local officer takes the call: the spy starts and he greets.
    await fake_ari.wait_for("POST", "/snoop")
    await fake_ari.wait_for("POST", "/play")
    await wait_until(lambda: _service_answered(attempt_id, call_id))
    record = service_call_of(await load(attempt_id), call_id)
    assert record["dialog"] and record["dialog"][0]["role"] == "caller"
    assert call.local and not call.ended


async def test_cloud_call_on_the_phone_rings_back_until_vapi_answers(
    cloud_manager: CloudCallManager,
    fake_ari: FakeAri,
    trainee_phone: None,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.telephony import cloud as cloud_module

    monkeypatch.setattr(cloud_module, "RINGBACK_BEFORE_VAPI_SECONDS", 0.2)
    manager = cloud_manager
    attempt_id = await phone_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="cloud")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    call = manager.call_for_service_call(call_id)
    assert isinstance(call, CloudCall) and call.ringback
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    ring = await fake_ari.wait_for("POST", "/play")
    assert ring.params["media"] == RINGBACK_MEDIA

    async def vapi_leg_dialled() -> bool:
        return any(
            r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
            for r in fake_ari.calls("POST", "/channels/create")
        )

    await wait_until(vapi_leg_dialled)
    # Vapi picks up: the ring-back stops and its leg joins the bridge.
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )
    await fake_ari.wait_for("DELETE", f"/playbacks/rb-{call.key}")
    await wait_until(lambda: _service_answered(attempt_id, call_id))
    assert call.ringback_id is None


async def test_inbound_call_takes_over_a_cloud_112_call(
    cloud_manager: CloudCallManager,
    fake_ari: FakeAri,
    trainee_phone: None,
):
    manager = cloud_manager
    attempt_id = await phone_attempt(dialog_mode="cloud")
    call = await manager.dial(attempt_id)
    assert isinstance(call, CloudCall) and call.phone == PHONE
    await fake_ari.emit(
        {"type": "StasisStart", "args": ["inbound", "79220000001"], "channel": {"id": "mf-in-4"}}
    )
    await fake_ari.wait_for("POST", "/channels/mf-in-4/answer")

    async def vapi_leg_dialled() -> bool:
        return any(
            r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
            for r in fake_ari.calls("POST", "/channels/create")
        )

    await wait_until(vapi_leg_dialled)
    await wait_until(lambda: _answered(attempt_id))
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )

    async def bridged() -> bool:
        added = [r.params.get("channel") for r in fake_ari.calls("POST", "/addChannel")]
        return "mf-in-4" in added and call.vapi_channel_id in added

    await wait_until(bridged)


# ---------------------------------------------------------------- helpers


async def _answered(attempt_id: uuid.UUID) -> bool:
    return (await load(attempt_id)).call_state == CALL_ANSWERED


async def _service_answered(attempt_id: uuid.UUID, call_id: str) -> bool:
    return bool(service_call_of(await load(attempt_id), call_id).get("answered"))


async def _service_ended(attempt_id: uuid.UUID, call_id: str) -> bool:
    return bool(service_call_of(await load(attempt_id), call_id).get("ended_at"))


async def test_finishing_the_lesson_hangs_up_the_phone(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    """The call on the phone has no «Завершить» in the card: the lesson's end hangs it up."""
    attempt_id = await phone_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None
    assert await manager.hangup_session(call.session_id) == 1
    assert [r.path for r in fake_ari.calls("DELETE", f"/channels/{call.channel_id}")]
    assert manager.call_for_attempt(attempt_id) is None
    assert await manager.hangup_session(call.session_id) == 0
