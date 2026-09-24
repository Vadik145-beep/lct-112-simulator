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
from app.models import CALL_ANSWERED, CALL_END_HANGUP, MODE_CARD_RESPONSE
from app.telephony import settings as telephony_settings
from app.telephony.ari import AriClient
from app.telephony.calls import VAR_MOBILE, CallManager
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


# ---------------------------------------------------------------- phones


def test_phones_are_normalized_for_multifon():
    normalize = telephony_settings.normalize_phone
    assert normalize("8 922 781-62-05") == "79227816205"
    assert normalize("+7 (922) 781-62-05") == "79227816205"
    assert normalize("9227816205") == "79227816205"
    assert normalize("112") == ""
    assert normalize("") == ""
    assert telephony_settings.same_phone("+79227816205", "89227816205")
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
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None and call.phone == PHONE
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == PHONE


async def test_without_a_phone_the_mobile_is_empty(manager: CallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt()
    call = await manager.dial(attempt_id)
    assert call is not None and call.phone == ""
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == ""


async def test_call_from_the_trainee_phone_takes_over_the_ringing_call(
    manager: CallManager, fake_ari: FakeAri, trainee_phone: None
):
    attempt_id = await make_attempt()
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
    attempt_id = await make_attempt()
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
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="buttons")
    call_id = await start_call(attempt_id)
    assert await manager.dial_service(attempt_id, call_id, "moek", "МОЭК") is True
    create = fake_ari.calls("POST", "/channels/create")[-1]
    assert create.body["variables"][VAR_MOBILE] == PHONE
    await fake_ari.emit(
        {"type": "StasisStart", "args": ["inbound", "89220000001"], "channel": {"id": "mf-in-3"}}
    )
    await fake_ari.wait_for("POST", "/channels/mf-in-3/answer")
    await wait_until(lambda: _service_answered(attempt_id, call_id))


# ---------------------------------------------------------------- calls of the card in the cloud


async def cloud_service_call(manager: CloudCallManager, fake_ari: FakeAri):
    attempt_id = await make_attempt(CARD, mode=MODE_CARD_RESPONSE, dialog_mode="cloud")
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


async def test_inbound_call_takes_over_a_cloud_112_call(
    cloud_manager: CloudCallManager,
    fake_ari: FakeAri,
    trainee_phone: None,
):
    manager = cloud_manager
    attempt_id = await make_attempt(dialog_mode="cloud")
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
