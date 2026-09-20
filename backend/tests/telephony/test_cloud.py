"""The cloud voice (plan/track-c-vapi.md) against the stand-in ARI server: in a lesson of the
``cloud`` mode the trainee is rung as before, on answer the Vapi leg is dialled and bridged
instead of the ExternalMedia spy, Vapi's server messages become dialog turns (with the reply
latency) and end the call; when the cloud fails, the local pipeline takes the call over.
Lessons in the other modes run locally under the same manager. Vapi's REST API is a stand-in
transport of httpx."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import get_settings
from app.dialog import service as dialog
from app.models import (
    CALL_ANSWERED,
    CALL_END_CALLER_HANGUP,
    CALL_END_HANGUP,
)
from app.telephony import cloud as cloud_module
from app.telephony import vapi as vapi_module
from app.telephony.ari import AriClient
from app.telephony.cloud import (
    EVENT_CLOUD_FALLBACK,
    VAPI_DIAL_ENDPOINT,
    VAR_TOKEN,
    VAR_VAPI_USER,
    CloudCall,
    CloudCallManager,
    new_token,
)
from app.telephony.vapi import (
    SECRET_HEADER,
    WEBHOOK_PATH,
    VapiClient,
    build_assistant,
    caller_prompt,
    server_config,
    sip_user_of,
    webhook_secret,
)
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import GAS_PIPE, make_attempt
from tests.telephony.fake_ari import FakeAri
from tests.telephony.test_calls import DROPS_CALL, _ended, events_of, load, wait_until

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)

PUBLIC_URL = "https://stand.example.org"
SIP_USER = "dds-trainer-abcd"


# ---------------------------------------------------------------- fixtures


class FakeVapiApi:
    """The resources of Vapi the trainer uses (numbers, assistants, a recording file), as an
    httpx transport."""

    RECORDING_URL = "https://files.example/rec.wav"
    RECORDING = b"RIFFfake-wav"

    def __init__(self, numbers: list[dict] | None = None) -> None:
        self.numbers = numbers or []
        self.assistants: dict[str, dict] = {}
        self.requests: list[tuple[str, str, dict | None]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        if str(request.url) == self.RECORDING_URL:
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=self.RECORDING)
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.method == "POST" and request.url.path == "/assistant":
            assistant = {"id": f"a{len(self.assistants) + 1}", **body}
            self.assistants[assistant["id"]] = assistant
            return httpx.Response(201, json=assistant)
        if request.method == "DELETE" and request.url.path.startswith("/assistant/"):
            removed = self.assistants.pop(request.url.path.rsplit("/", 1)[-1], None)
            return httpx.Response(200 if removed else 404, json=removed or {"message": "no"})
        if request.method == "GET" and request.url.path == "/phone-number":
            return httpx.Response(200, json=self.numbers)
        if request.method == "POST" and request.url.path == "/phone-number":
            number = {"id": f"n{len(self.numbers) + 1}", **body}
            self.numbers.append(number)
            return httpx.Response(201, json=number)
        if request.method == "PATCH" and request.url.path.startswith("/phone-number/"):
            number_id = request.url.path.rsplit("/", 1)[-1]
            for number in self.numbers:
                if number["id"] == number_id:
                    number.update(body)
                    return httpx.Response(200, json=number)
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(404, json={"message": "unknown"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
async def fake_ari() -> AsyncIterator[FakeAri]:
    fake = FakeAri()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def cloud_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_public_url", PUBLIC_URL)
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", None)
    monkeypatch.setattr(settings, "vapi_number_name", "dds-trainer")


@pytest.fixture
async def manager(fake_ari: FakeAri, cloud_settings: None) -> AsyncIterator[CloudCallManager]:
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
def opening_audio(monkeypatch: pytest.MonkeyPatch) -> None:
    """A voiced opening on disk, so the local pipeline has something to play."""
    from pathlib import Path

    from app.telephony import media

    folder = Path(get_settings().storage_dir) / "tts" / "fake-cloud" / "v1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "opening.wav").write_bytes(media.pcm_to_wav(bytes(3200)))
    (folder / "opening.sln16").write_bytes(bytes(3200))

    async def fake_opening_audio(version, scenario):
        return "tts/fake-cloud/v1/opening.wav"

    monkeypatch.setattr(dialog, "opening_audio", fake_opening_audio)
    monkeypatch.setattr(media, "to_asterisk_sound", lambda source: source.with_suffix(".sln16"))


async def answered_call(manager: CloudCallManager, fake_ari: FakeAri, scenario: str = GAS_PIPE):
    """A call of a cloud lesson whose trainee answered: the Vapi leg is being dialled."""
    attempt_id = await make_attempt(scenario, dialog_mode="cloud")
    call = await manager.dial(attempt_id)
    assert isinstance(call, CloudCall)
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    creates = []

    async def vapi_leg_dialled() -> bool:
        creates[:] = [
            r
            for r in fake_ari.calls("POST", "/channels/create")
            if r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
        ]
        return bool(creates)

    await wait_until(vapi_leg_dialled)
    return attempt_id, call, creates[-1]


def message(kind: str, call: CloudCall, **extra) -> dict:
    return {
        "type": kind,
        "call": {"id": f"vapi-{call.key}", "customer": {"number": f"+{call.token}"}},
        **extra,
    }


# ---------------------------------------------------------------- the call


async def test_answer_dials_vapi_leg_and_bridges_it(manager: CloudCallManager, fake_ari: FakeAri):
    attempt_id, call, create = await answered_call(manager, fake_ari)
    assert create.params["formats"] == "slin16"
    assert create.body["variables"][VAR_TOKEN] == call.token
    assert create.body["variables"][VAR_VAPI_USER] == SIP_USER
    assert call.token.isdigit() and len(call.token) == 10
    # Recording as before; no ExternalMedia spy, no opening playback: Vapi speaks it.
    await fake_ari.wait_for("POST", "/record")
    assert not fake_ari.calls("POST", "/channels/externalMedia")
    assert not fake_ari.calls("POST", "/snoop")
    await wait_until(lambda: _answered(attempt_id))
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_ANSWERED
    assert attempt.dialog[0]["method"] == "opening"
    assert attempt.recording_path == f"recordings/{attempt_id.hex}.wav"
    assert not fake_ari.calls("POST", "/play")

    # Vapi answers: its leg joins the bridge of the trainee.
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _bridged(fake_ari, call))
    assert call.vapi_answered
    assert manager.call_for_token(call.token) is call

    # The trainee hangs up: both legs are torn down.
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id))
    attempt = await load(attempt_id)
    assert attempt.call_end_reason == CALL_END_HANGUP
    hung_up = [r.path for r in fake_ari.calls("DELETE", "/channels/")]
    assert any(path.endswith(call.vapi_channel_id) for path in hung_up)
    assert manager.call_for_token(call.token) is None
    assert manager.call_for_attempt(attempt_id) is None


async def test_vapi_leg_failure_falls_back_to_the_local_pipeline(
    manager: CloudCallManager, fake_ari: FakeAri, opening_audio: None
):
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    await fake_ari.emit(
        {
            "type": "Dial",
            "dialstatus": "CONGESTION",
            "peer": {"id": call.vapi_channel_id},
            "caller": {"id": call.channel_id},
        }
    )
    await wait_until(lambda: _fell_back(attempt_id))
    # The same call goes on: the spy and the pipeline start, the opening is played by us
    # (Vapi never said it), the Vapi leg is hung up and forgotten.
    await fake_ari.wait_for("POST", "/channels/externalMedia")
    await fake_ari.wait_for("POST", "/snoop")
    await fake_ari.wait_for("POST", "/play")
    assert call.local and not call.ended
    assert manager.call_for_token(call.token) is None
    assert (await load(attempt_id)).call_state == CALL_ANSWERED
    # Vapi's messages for this call are ignored from now on.
    assert await manager.webhook(message("transcript", call, role="user", transcript="x")) == {}
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id))
    assert (await load(attempt_id)).call_end_reason == CALL_END_HANGUP


async def test_without_vapi_number_the_call_runs_locally(
    manager: CloudCallManager, fake_ari: FakeAri
):
    manager.sip_user = None
    attempt_id = await make_attempt(dialog_mode="cloud")
    call = await manager.dial(attempt_id)
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _fell_back(attempt_id))
    await fake_ari.wait_for("POST", "/snoop")
    assert not any(
        r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
        for r in fake_ari.calls("POST", "/channels/create")
    )
    await wait_until(lambda: _answered(attempt_id))


async def test_vapi_leg_dropped_mid_call_falls_back_after_the_grace(
    manager: CloudCallManager,
    fake_ari: FakeAri,
    opening_audio: None,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(cloud_module, "LEG_LOST_GRACE_SECONDS", 0.05)
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _bridged(fake_ari, call))
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.vapi_channel_id}}
    )
    await wait_until(lambda: _fell_back(attempt_id))
    await fake_ari.wait_for("POST", "/snoop")
    # The opening was heard already: not played again.
    assert not fake_ari.calls("POST", "/play")
    assert call.local and not call.ended


async def test_vapi_leg_dropped_after_vapi_ended_is_a_caller_hangup(
    manager: CloudCallManager, fake_ari: FakeAri
):
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.vapi_channel_id, "state": "Up"}}
    )
    await wait_until(lambda: _bridged(fake_ari, call))
    await manager.webhook(
        message("status-update", call, status="ended", endedReason="assistant-ended-call")
    )
    await fake_ari.emit(
        {"type": "ChannelDestroyed", "cause": 16, "channel": {"id": call.vapi_channel_id}}
    )
    await wait_until(lambda: _ended(attempt_id))
    assert (await load(attempt_id)).call_end_reason == CALL_END_CALLER_HANGUP
    assert EVENT_CLOUD_FALLBACK not in await events_of(attempt_id)


async def test_lesson_in_select_mode_runs_locally(
    manager: CloudCallManager, fake_ari: FakeAri, opening_audio: None
):
    attempt_id = await make_attempt(dialog_mode="select")
    call = await manager.dial(attempt_id)
    await fake_ari.emit(
        {"type": "ChannelStateChange", "channel": {"id": call.channel_id, "state": "Up"}}
    )
    await fake_ari.wait_for("POST", "/snoop")
    await fake_ari.wait_for("POST", "/play")
    await wait_until(lambda: _answered(attempt_id))
    assert not any(
        r.params.get("endpoint") == VAPI_DIAL_ENDPOINT
        for r in fake_ari.calls("POST", "/channels/create")
    )
    assert call.vapi_channel_id is None and not call.local
    assert EVENT_CLOUD_FALLBACK not in await events_of(attempt_id)


# ---------------------------------------------------------------- server messages


async def test_assistant_request_answers_with_the_scenario(
    manager: CloudCallManager, fake_ari: FakeAri
):
    attempt_id, call, _ = await answered_call(manager, fake_ari, DROPS_CALL)
    await wait_until(lambda: _answered(attempt_id))
    attempt = await load(attempt_id)
    response = await manager.webhook(message("assistant-request", call))
    assistant = response["assistant"]
    assert assistant["firstMessage"] == attempt.dialog[0]["text"]
    prompt = assistant["model"]["messages"][0]["content"]
    assert "ЗАЯВИТЕЛЯ" in prompt and "бросаешь трубку" in prompt
    assert assistant["server"]["url"] == f"{PUBLIC_URL}{WEBHOOK_PATH}"
    assert assistant["server"]["headers"][SECRET_HEADER] == webhook_secret()
    assert assistant["transcriber"]["languages"] == ["ru"]
    assert "transcript" in assistant["serverMessages"]
    # The Vapi call id is remembered: later messages need no token.
    assert call.vapi_call_id == f"vapi-{call.key}"
    assert manager.call_for_message({"call": {"id": call.vapi_call_id}}) is call


async def test_unknown_call_gets_a_spoken_error(manager: CloudCallManager):
    response = await manager.webhook(
        {"type": "assistant-request", "call": {"id": "x", "customer": {"number": "+70000000000"}}}
    )
    assert "error" in response
    assert await manager.webhook({"type": "transcript", "call": {"id": "x"}}) == {}


async def test_transcripts_become_turns_and_vapi_ends_the_call(
    manager: CloudCallManager, fake_ari: FakeAri
):
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    opening = (await load(attempt_id)).dialog[0]["text"]
    await manager.webhook(message("assistant-request", call))
    # The opening Vapi spoke is turn 0 already and is not stored twice.
    await manager.webhook(
        message("transcript", call, role="assistant", transcriptType="final", transcript=opening)
    )
    await manager.webhook(
        message("transcript", call, role="user", transcriptType="partial", transcript="Диктуйте")
    )
    await manager.webhook(
        message(
            "transcript", call, role="user", transcriptType="final", transcript="Диктуйте адрес"
        )
    )
    await manager.webhook(
        message(
            "transcript",
            call,
            role="assistant",
            transcriptType="final",
            transcript="Улица Ленина, дом пять.",
        )
    )
    attempt = await load(attempt_id)
    turns = attempt.dialog
    assert [t["role"] for t in turns] == ["caller", "operator", "caller"]
    assert turns[1]["text"] == "Диктуйте адрес" and turns[1]["heard"] is True
    assert "address" in turns[1]["topics"]
    assert turns[2]["method"] == dialog.EXTERNAL_METHOD
    assert (await events_of(attempt_id)).count("dialog.turn") == 2

    # The final report carries a phrase the live messages missed; then the call ends.
    await manager.webhook(
        message(
            "end-of-call-report",
            call,
            endedReason="assistant-ended-call",
            artifact={
                "messages": [
                    {"role": "system", "message": "prompt"},
                    {"role": "bot", "message": opening},
                    {"role": "user", "message": "Диктуйте адрес"},
                    {"role": "bot", "message": "Улица Ленина, дом пять."},
                    {"role": "user", "message": "Помощь направлена."},
                ]
            },
        )
    )
    await wait_until(lambda: _ended(attempt_id))
    attempt = await load(attempt_id)
    assert [t["text"] for t in attempt.dialog[1:]] == [
        "Диктуйте адрес",
        "Улица Ленина, дом пять.",
        "Помощь направлена.",
    ]
    assert attempt.call_end_reason == CALL_END_CALLER_HANGUP
    assert manager.call_for_message({"call": {"id": call.vapi_call_id}}) is None


async def test_speech_updates_measure_the_reply_latency(
    manager: CloudCallManager, fake_ari: FakeAri
):
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    await manager.webhook(message("speech-update", call, role="user", status="started"))
    await manager.webhook(message("speech-update", call, role="user", status="stopped"))
    await manager.webhook(
        message("transcript", call, role="user", transcriptType="final", transcript="Адрес?")
    )
    await asyncio.sleep(0.05)
    await manager.webhook(message("speech-update", call, role="assistant", status="started"))
    await manager.webhook(
        message("transcript", call, role="assistant", transcriptType="final", transcript="Ленина")
    )
    turns = (await load(attempt_id)).dialog
    assert turns[1].get("latency_ms") is None
    assert turns[2]["latency_ms"] >= 50
    assert call.latencies_ms == [turns[2]["latency_ms"]]
    # The next reply without a measured start carries no latency.
    await manager.webhook(
        message("transcript", call, role="assistant", transcriptType="final", transcript="Ещё")
    )
    assert (await load(attempt_id)).dialog[3].get("latency_ms") is None


async def test_status_ended_by_silence_is_a_caller_hangup(
    manager: CloudCallManager, fake_ari: FakeAri
):
    attempt_id, call, _ = await answered_call(manager, fake_ari)
    await wait_until(lambda: _answered(attempt_id))
    await manager.webhook(
        message("status-update", call, status="ended", endedReason="silence-timed-out")
    )
    await wait_until(lambda: _ended(attempt_id))
    assert (await load(attempt_id)).call_end_reason == CALL_END_CALLER_HANGUP


async def test_token_is_found_in_sip_uri_and_template_variables(
    manager: CloudCallManager, fake_ari: FakeAri
):
    _, call, _ = await answered_call(manager, fake_ari)
    by_uri = {"call": {"customer": {"sipUri": f"sip:{call.token}@203.0.113.5:5061"}}}
    assert manager.call_for_message(by_uri) is call
    by_header = {"call": {"assistantOverrides": {"variableValues": {"call_token": call.token}}}}
    assert manager.call_for_message(by_header) is call
    assert manager.call_for_message({"call": {"customer": {"number": "+7999"}}}) is None


# ---------------------------------------------------------------- the assistant and the client


def test_caller_prompt_and_assistant(cloud_settings: None, monkeypatch: pytest.MonkeyPatch):
    from app.domain.evaluation.schemas import CallIntakeScenario

    scenario = CallIntakeScenario.model_validate(
        {
            "kind": "call_intake",
            "title": "Запах газа в подъезде",
            "caller": {
                "persona": "panicked",
                "opening": "Алло, у нас газом пахнет!",
                "facts": {"address": "Ленина 5", "floor": "3"},
                "behaviour": "кричит",
            },
            "required_topics": ["address"],
            "reference_card": {"incident_type": "Запах газа"},
        }
    )
    prompt = caller_prompt(scenario)
    assert "panicked" in prompt and "кричит" in prompt
    assert "- address: Ленина 5" in prompt and "- floor: 3" in prompt
    assert "бросаешь трубку" not in prompt
    # The emotional state follows the persona and asks for expressive, punctuated speech.
    assert "Ты в панике" in prompt and "многоточи" in prompt
    assert "словами, а не цифрами" in prompt
    from app.telephony.vapi import caller_state

    assert caller_state("elderly_calm") == "calm" and caller_state("mother_anxious") == "anxious"
    assert caller_state("angry_customer") == "angry" and caller_state(None) == "calm"
    assistant = build_assistant(scenario)
    assert assistant["firstMessage"] == "Алло, у нас газом пахнет!"
    assert assistant["model"]["provider"] == "openai"
    assert assistant["voice"]["provider"] == "11labs"
    assert assistant["artifactPlan"] == {"recordingEnabled": False}
    assert assistant["endCallFunctionEnabled"] is True
    assert "speech-update" in assistant["serverMessages"]
    assert assistant["transcriber"] == {
        "provider": "soniox",
        "model": "stt-rt-v5",
        "languages": ["ru"],
    }
    from app.telephony.vapi import transcriber_config

    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_transcriber_provider", "deepgram")
    monkeypatch.setattr(settings, "cloud_voice_transcriber_model", "nova-2")
    assert transcriber_config() == {"provider": "deepgram", "model": "nova-2", "language": "ru"}
    assert build_assistant(scenario, recording=True)["artifactPlan"] == {"recordingEnabled": True}


def test_voice_follows_the_scenario(cloud_settings: None, monkeypatch: pytest.MonkeyPatch):
    from app.domain.evaluation.schemas import CallIntakeScenario
    from app.telephony.vapi import STABILITY_AGITATED, STABILITY_CALM, voice_config

    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_voice_id", "default-voice")
    monkeypatch.setattr(settings, "cloud_voice_voice_id_female", "female-voice")
    monkeypatch.setattr(settings, "cloud_voice_voice_id_elder_male", "elder-voice")
    monkeypatch.setattr(settings, "cloud_voice_voice_id_male", None)

    def scenario(voice: str | None, persona: str) -> CallIntakeScenario:
        return CallIntakeScenario.model_validate(
            {
                "kind": "call_intake",
                "title": "t",
                "caller": {"persona": persona, "voice": voice, "opening": "Алло"},
                "required_topics": [],
                "reference_card": {"incident_type": "x"},
            }
        )

    calm_woman = voice_config(scenario("ru_female_1", "calm_commuter"))
    assert calm_woman["voiceId"] == "female-voice" and calm_woman["stability"] == STABILITY_CALM
    # The elderly woman falls back to the female voice and speaks slower; the elderly man
    # has his own voice.
    elderly_woman = voice_config(scenario("ru_female_2", "elderly_panicked"))
    assert elderly_woman["voiceId"] == "female-voice"
    assert elderly_woman["speed"] == 1.12 and elderly_woman["stability"] == STABILITY_AGITATED
    assert voice_config(scenario("ru_male_3", "elderly_calm"))["voiceId"] == "elder-voice"
    # No male voice configured: the default; a panicked persona wavers, hurries, is styled.
    panicked_man = voice_config(scenario("ru_male_4", "victim_panicked"))
    assert panicked_man["voiceId"] == "default-voice"
    assert panicked_man["stability"] == STABILITY_AGITATED
    assert panicked_man["style"] > calm_woman["style"] and panicked_man["speed"] > 1
    assert voice_config(scenario(None, "calm"))["voiceId"] == "default-voice"


def test_webhook_secret_is_stable_and_configurable(monkeypatch: pytest.MonkeyPatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", None)
    derived = webhook_secret()
    assert derived == webhook_secret() and len(derived) == 40
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", "explicit")
    assert webhook_secret() == "explicit"
    monkeypatch.setattr(settings, "cloud_voice_public_url", "https://h.example/")
    assert server_config()["url"] == f"https://h.example{WEBHOOK_PATH}"
    assert sip_user_of("sip:dds-trainer-1a2b@sip.vapi.ai") == "dds-trainer-1a2b"
    assert new_token() != new_token()


async def test_client_creates_or_repoints_the_number(cloud_settings: None):
    server = server_config()
    api = FakeVapiApi()
    client = VapiClient("https://api.example", "test-key", transport=api.transport())
    user = await client.ensure_sip_number("dds-trainer", "sip.vapi.ai", server)
    assert user.startswith("dds-trainer-")
    created = api.numbers[0]
    assert created["provider"] == "vapi" and created["sipUri"] == f"sip:{user}@sip.vapi.ai"
    assert created["server"] == server and "assistantId" not in created

    # Second start: the number exists and is reused; a stale server url is re-pointed.
    api.numbers[0]["server"] = {"url": "https://old.example/hook", "headers": {}}
    assert await client.ensure_sip_number("dds-trainer", "sip.vapi.ai", server) == user
    assert api.numbers[0]["server"] == server
    assert [m for m, _, _ in api.requests] == ["GET", "POST", "GET", "PATCH"]
    await client.aclose()

    failing = VapiClient(
        "https://api.example",
        "test-key",
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "no"})),
    )
    with pytest.raises(vapi_module.VapiError):
        await failing.phone_numbers()
    await failing.aclose()


async def test_setup_without_key_or_url_is_a_warning(
    fake_ari: FakeAri, cloud_settings: None, monkeypatch: pytest.MonkeyPatch
):
    ari = AriClient(f"http://127.0.0.1:{fake_ari.port}/ari", "trainer", "trainer", "trainer")
    manager = CloudCallManager(ari, vapi=None)
    assert await manager.setup() is False
    api = FakeVapiApi()
    manager = CloudCallManager(
        ari, VapiClient("https://api.example", "test-key", transport=api.transport())
    )
    assert await manager.setup() is True
    assert manager.sip_user and manager.sip_user.startswith("dds-trainer-")
    monkeypatch.setattr(get_settings(), "cloud_voice_public_url", None)
    assert await manager.setup() is False
    await ari.aclose()


# ---------------------------------------------------------------- helpers


async def _answered(attempt_id: uuid.UUID) -> bool:
    return (await load(attempt_id)).call_state == CALL_ANSWERED


async def _fell_back(attempt_id: uuid.UUID) -> bool:
    return EVENT_CLOUD_FALLBACK in await events_of(attempt_id)


async def _bridged(fake_ari: FakeAri, call: CloudCall) -> bool:
    return any(
        r.path.endswith(f"/bridges/{call.bridge_id}/addChannel")
        and r.params.get("channel") == call.vapi_channel_id
        for r in fake_ari.calls("POST", "/addChannel")
    )
