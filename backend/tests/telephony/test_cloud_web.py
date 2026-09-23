"""Cloud calls from the browser (plan/track-c-vapi.md, «без телефонии»): the assistant of
the scenario is stored in Vapi and handed to the browser by id, the server messages of that
assistant become dialog turns, the end deletes the assistant and keeps the recording.
Vapi's REST API is a stand-in transport of httpx."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.dialog import service as dialog
from app.models import (
    CALL_ANSWERED,
    CALL_END_CALLER_HANGUP,
    CALL_END_HANGUP,
    CALL_ENDED,
    MODE_CARD_RESPONSE,
)
from app.telephony import cloud_web
from app.telephony import service as telephony
from app.telephony.cloud import EVENT_CLOUD_FALLBACK
from app.telephony.cloud_web import CloudWebCalls
from app.telephony.vapi import SECRET_HEADER, WEBHOOK_PATH, VapiClient, webhook_secret
from tests.api.conftest import DATA_DIR
from tests.api.test_dialog import GAS_PIPE, make_attempt
from tests.conftest import bearer, login
from tests.telephony.test_calls import events_of, load
from tests.telephony.test_cloud import PUBLIC_URL, FakeVapiApi, scenario_opening
from tests.telephony.test_service_calls import start_call as start_service_call

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


@pytest.fixture
def api() -> FakeVapiApi:
    return FakeVapiApi()


@pytest.fixture
async def web(api: FakeVapiApi, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CloudWebCalls]:
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_public_url", PUBLIC_URL)
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", None)
    monkeypatch.setattr(settings, "vapi_public_key", "pub-key")
    monkeypatch.setattr(telephony, "_service", None)
    calls = CloudWebCalls(VapiClient("https://api.example", "test-key", transport=api.transport()))
    monkeypatch.setattr(cloud_web, "_calls", calls)
    yield calls
    await calls.shutdown()


async def start_call(client: AsyncClient, attempt_id) -> tuple[dict, dict]:
    student = await login(client, "student1")
    r = await client.post(f"/api/attempts/{attempt_id}/cloud-call", headers=bearer(student))
    assert r.status_code == 200, r.text
    return r.json(), student


def hook(client: AsyncClient, message: dict):
    return client.post(
        WEBHOOK_PATH, json={"message": message}, headers={SECRET_HEADER: webhook_secret()}
    )


# ---------------------------------------------------------------- starting


async def test_start_stores_the_assistant_and_answers_the_call(
    client: AsyncClient, web: CloudWebCalls, api: FakeVapiApi
):
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    keys, student = await start_call(client, attempt_id)
    assert keys["public_key"] == "pub-key" and keys["api_url"] == "https://api.example"
    assert keys["assistant_id"] in api.assistants and len(keys["token"]) == 10
    stored = api.assistants[keys["assistant_id"]]
    # The facts stay on the server: the browser gets an id, Vapi records, reports come here.
    assert "messages" in stored["model"] and stored["artifactPlan"] == {"recordingEnabled": True}
    assert stored["server"]["url"] == f"{PUBLIC_URL}{WEBHOOK_PATH}"
    assert stored["name"].startswith("web-")
    attempt = await load(attempt_id)
    # Приветствие скажет Vapi: своей реплики не пишем (замечание пользователя 22.09.2026).
    assert attempt.call_state == CALL_ANSWERED and attempt.dialog == []
    # Asking again is idempotent: the same assistant, no second one in the account.
    again = await client.post(f"/api/attempts/{attempt_id}/cloud-call", headers=bearer(student))
    assert again.json()["assistant_id"] == keys["assistant_id"] and len(api.assistants) == 1
    # The panel's dialog says the caller is in the cloud.
    d = await client.get(f"/api/attempts/{attempt_id}/dialog", headers=bearer(student))
    assert d.json()["mode"] == "cloud" and d.json()["requested_mode"] == "cloud"
    assert d.json()["fallback_replies"] == 0


async def test_service_call_is_played_by_the_cloud(
    client: AsyncClient, web: CloudWebCalls, api: FakeVapiApi
):
    """Звонок диспетчера дежурному в облачном занятии ведёт Vapi: ассистент собирается из
    реплик службы, реплики ложатся в стенограмму этого звонка, а не карточки (issue #59)."""
    attempt_id = await make_attempt(
        "card_moek_1_net_otopleniya", mode=MODE_CARD_RESPONSE, dialog_mode="cloud"
    )
    student = await login(client, "student1")
    call_id = await start_service_call(attempt_id)

    keys = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/cloud-call", headers=bearer(student)
    )
    assert keys.status_code == 200, keys.text
    assistant = api.assistants[keys.json()["assistant_id"]]
    prompt = assistant["model"]["messages"][0]["content"]
    # Роль именно дежурного службы: облако принимает информацию, а не допрашивает
    # диспетчера, как заявителя в приёме вызова (замечание пользователя 23.09.2026).
    assert prompt.startswith("Ты дежурный службы")
    assert "ЗАЯВИТЕЛЯ" not in prompt
    assert "номер наряда: наряд твой" in prompt
    assert "где он находится" in prompt
    # Трубку кладёт диспетчер: дежурному функция завершения звонка не даётся.
    assert assistant["endCallFunctionEnabled"] is False
    assert "на другой язык" in prompt and "Не кладёшь трубку" in prompt
    # Замечания с живого прогона 23.09.2026: адрес проговаривается один раз, прощания нет,
    # на «хорошо» дежурный молчит.
    assert "Второй раз адрес не проговариваешь НИКОГДА" in prompt
    assert "goodbye" in prompt.lower()
    assert "НЕ отвечаешь ничего" in prompt
    assert "как губка" in prompt
    assert "НИЧЕГО НЕ ПРИДУМЫВАЕШЬ" in prompt
    # Дежурный знает номер своего наряда и называет его сам.
    assert "твой наряд" in prompt
    # Не отвечает на каждую паузу диспетчера: выдержка перед репликой.
    assert assistant["startSpeakingPlan"] == {"waitSeconds": 1.5}
    # Первую фразу говорит облако: своей реплики в записи звонка нет.
    attempt = await load(attempt_id)
    record = next(c for c in attempt.service_calls if c["id"] == call_id)
    assert record["answered"] is True and record["dialog"] == []

    # Реплики из облака попадают в стенограмму звонка, а не в диалог карточки.
    for role, text in (
        ("user", "Улица Молостовых, дом 10, нет отопления, наряд 4127"),
        ("assistant", "Принял, бригада тепловых сетей выезжает."),
    ):
        r = await hook(
            client,
            {
                "type": "transcript",
                "transcriptType": "final",
                "role": role,
                "transcript": text,
                "assistant": {"id": keys.json()["assistant_id"]},
            },
        )
        assert r.status_code == 200, r.text
    attempt = await load(attempt_id)
    record = next(c for c in attempt.service_calls if c["id"] == call_id)
    assert [t["role"] for t in record["dialog"]] == ["operator", "caller"]
    assert "Молостовых" in record["dialog"][0]["text"]
    assert record["dialog"][0]["method"] == "cloud" and record["dialog"][0]["heard"] is True
    assert attempt.dialog == []  # карточка ДДС своего диалога не ведёт
    assert record["facts_passed"], "переданные факты считаются и в облачном звонке"


async def test_service_cloud_call_refuses_a_local_lesson(client: AsyncClient, web: CloudWebCalls):
    attempt_id = await make_attempt(
        "card_moek_1_net_otopleniya", mode=MODE_CARD_RESPONSE, dialog_mode="select"
    )
    student = await login(client, "student1")
    call_id = await start_service_call(attempt_id)
    r = await client.post(
        f"/api/attempts/{attempt_id}/service-call/{call_id}/cloud-call", headers=bearer(student)
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_cloud_lesson"


async def test_start_refuses_other_lessons_and_telephony(
    client: AsyncClient, web: CloudWebCalls, monkeypatch: pytest.MonkeyPatch
):
    student = await login(client, "student1")
    local = await make_attempt(GAS_PIPE, dialog_mode="select")
    r = await client.post(f"/api/attempts/{local}/cloud-call", headers=bearer(student))
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_cloud_lesson"
    cloud = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    monkeypatch.setattr(telephony, "telephony_active", lambda: True)
    r = await client.post(f"/api/attempts/{cloud}/cloud-call", headers=bearer(student))
    assert r.status_code == 409 and r.json()["error"]["code"] == "telephony_active"


async def test_unreachable_cloud_is_503_and_a_fallback_event(
    client: AsyncClient, web: CloudWebCalls, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "vapi_public_key", None)
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    student = await login(client, "student1")
    r = await client.post(f"/api/attempts/{attempt_id}/cloud-call", headers=bearer(student))
    assert r.status_code == 503 and r.json()["error"]["code"] == "cloud_unavailable"
    assert "VAPI_PUBLIC_KEY" in r.json()["error"]["message"]
    assert EVENT_CLOUD_FALLBACK in await events_of(attempt_id)
    # The call is answered anyway: the panel continues with the microphone and the stand-by.
    assert (await load(attempt_id)).call_state == CALL_ANSWERED
    say = await client.post(
        f"/api/attempts/{attempt_id}/say", headers=bearer(student), json={"text": "Адрес?"}
    )
    assert say.status_code == 200
    assert say.json()["dialog"]["fallback_replies"] == 1


async def test_browser_failure_is_noted(client: AsyncClient, web: CloudWebCalls, api: FakeVapiApi):
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    keys, student = await start_call(client, attempt_id)
    r = await client.post(
        f"/api/attempts/{attempt_id}/cloud-call/failed",
        headers=bearer(student),
        json={"reason": "microphone denied"},
    )
    # Ответ несёт приветствие, которое панель проиграет вместо облачного заявителя.
    assert r.status_code == 200
    assert r.json()["text"] == await scenario_opening(attempt_id)
    assert (await load(attempt_id)).dialog[0]["method"] == "opening"
    assert EVENT_CLOUD_FALLBACK in await events_of(attempt_id)
    assert keys["assistant_id"] not in api.assistants
    assert web.call_for_attempt(attempt_id) is None


# ---------------------------------------------------------------- server messages


async def test_messages_by_assistant_id_become_turns_and_end_the_call(
    client: AsyncClient, web: CloudWebCalls, api: FakeVapiApi, tmp_path, monkeypatch
):
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path))
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    keys, student = await start_call(client, attempt_id)
    call = {"id": "web-call-1", "assistantId": keys["assistant_id"], "type": "webCall"}
    opening = await scenario_opening(attempt_id)
    for message in (
        {"type": "transcript", "role": "assistant", "transcript": opening, "call": call},
        {"type": "speech-update", "role": "user", "status": "stopped", "call": call},
        {"type": "transcript", "role": "user", "transcript": "Диктуйте адрес", "call": call},
        {"type": "speech-update", "role": "assistant", "status": "started", "call": call},
        {"type": "transcript", "role": "assistant", "transcript": "Ленина, пять", "call": call},
    ):
        assert (await hook(client, message)).status_code == 200
    turns = (await load(attempt_id)).dialog
    assert [t["role"] for t in turns] == ["caller", "operator", "caller"]
    assert turns[0]["text"] == opening and turns[0]["method"] == dialog.EXTERNAL_METHOD
    assert turns[1]["heard"] and "address" in turns[1]["topics"]
    assert turns[2]["method"] == dialog.EXTERNAL_METHOD and turns[2]["latency_ms"] >= 0
    # Later messages are matched by the call id alone.
    assert web.call_for_message({"call": {"id": "web-call-1"}}) is web.call_for_attempt(attempt_id)

    report = {
        "type": "end-of-call-report",
        "endedReason": "assistant-ended-call",
        "call": call,
        "artifact": {
            "recordingUrl": FakeVapiApi.RECORDING_URL,
            "messages": [
                {"role": "bot", "message": opening},
                {"role": "user", "message": "Диктуйте адрес"},
                {"role": "bot", "message": "Ленина, пять"},
                {"role": "user", "message": "Помощь направлена."},
            ],
        },
    }
    assert (await hook(client, report)).status_code == 200
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_ENDED
    assert attempt.call_end_reason == CALL_END_CALLER_HANGUP
    assert [t["text"] for t in attempt.dialog[1:]] == [
        "Диктуйте адрес",
        "Ленина, пять",
        "Помощь направлена.",
    ]
    assert attempt.recording_path == f"recordings/web-{attempt_id.hex}.wav"
    assert (tmp_path / attempt.recording_path).read_bytes() == FakeVapiApi.RECORDING
    assert keys["assistant_id"] not in api.assistants
    rec = await client.get(f"/api/attempts/{attempt_id}/recording", headers=bearer(student))
    assert rec.status_code == 200 and rec.content == FakeVapiApi.RECORDING


async def test_hangup_in_the_panel_deletes_the_assistant(
    client: AsyncClient, web: CloudWebCalls, api: FakeVapiApi
):
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    keys, student = await start_call(client, attempt_id)
    r = await client.post(f"/api/attempts/{attempt_id}/hangup", headers=bearer(student))
    assert r.status_code == 200
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_ENDED and attempt.call_end_reason == CALL_END_HANGUP
    assert keys["assistant_id"] not in api.assistants
    # A late message of that call is ignored.
    late = {
        "type": "transcript",
        "role": "user",
        "transcript": "x",
        "call": {"assistantId": keys["assistant_id"]},
    }
    assert (await hook(client, late)).json() == {}
    assert (await load(attempt_id)).dialog == []


async def test_status_ended_by_the_trainee_side(client: AsyncClient, web: CloudWebCalls):
    attempt_id = await make_attempt(GAS_PIPE, dialog_mode="cloud")
    keys, _ = await start_call(client, attempt_id)
    ended = {
        "type": "status-update",
        "status": "ended",
        "endedReason": "customer-ended-call",
        "call": {
            "id": "c9",
            "assistantOverrides": {"variableValues": {"callToken": keys["token"]}},
        },
    }
    assert (await hook(client, ended)).status_code == 200
    attempt = await load(attempt_id)
    assert attempt.call_state == CALL_ENDED and attempt.call_end_reason == CALL_END_HANGUP
