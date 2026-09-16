"""LlamaCppChat against a mocked llama.cpp server, and the ALLOW_EXTERNAL_AI start-up check."""

from __future__ import annotations

import json

import httpx
import pytest

from app.config import Settings
from app.providers import llm as llm_module
from app.providers.llm import (
    LlamaCppChat,
    ModelOutputError,
    ModelUnavailableError,
    parse_json_object,
)

BASE_ENV = {
    "secret_key": "x" * 32,
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "database_admin_url": "postgresql+asyncpg://u:p@localhost/db",
}


def _server(content: str, slots: int = 4, status: int = 200) -> tuple[httpx.MockTransport, list]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/health":
            return httpx.Response(status, json={"status": "ok"})
        if request.url.path == "/props":
            return httpx.Response(200, json={"total_slots": slots})
        assert request.url.path == "/v1/chat/completions"
        if status != 200:
            return httpx.Response(status, text="boom")
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    return httpx.MockTransport(handler), seen


async def test_complete_json_sends_schema_cache_and_slot() -> None:
    transport, seen = _server('{"reply_id": 3}', slots=4)
    client = LlamaCppChat("http://llm:8080", transport=transport)
    schema = {"type": "object", "properties": {"reply_id": {"enum": [1, 2, 3, None]}}}
    answer = await client.complete_json(
        [{"role": "user", "content": "Адрес?"}], schema, max_tokens=12, slot_key="attempt-7"
    )
    assert answer == {"reply_id": 3}
    chat = next(r for r in seen if r.url.path == "/v1/chat/completions")
    payload = json.loads(chat.content)
    assert payload["response_format"] == {"type": "json_object", "schema": schema}
    assert payload["cache_prompt"] is True
    assert payload["max_tokens"] == 12
    assert payload["temperature"] == 0.0
    assert 0 <= payload["id_slot"] < 4


async def test_same_conversation_keeps_its_slot() -> None:
    transport, seen = _server('{"reply_id": 1}', slots=3)
    client = LlamaCppChat("http://llm:8080", transport=transport)
    for _ in range(2):
        await client.complete_json([], {}, max_tokens=5, slot_key="attempt-1")
    slots = [json.loads(r.content)["id_slot"] for r in seen if r.url.path.endswith("completions")]
    assert slots[0] == slots[1]
    # /props is asked once, not per request.
    assert sum(r.url.path == "/props" for r in seen) == 1


async def test_without_slot_key_no_slot_is_pinned() -> None:
    transport, seen = _server('{"ok": true}')
    client = LlamaCppChat("http://llm:8080", transport=transport)
    await client.complete_json([], {}, max_tokens=5)
    payload = json.loads(seen[-1].content)
    assert "id_slot" not in payload


async def test_broken_json_raises_output_error() -> None:
    transport, _ = _server("{oops")
    client = LlamaCppChat("http://llm:8080", transport=transport)
    with pytest.raises(ModelOutputError):
        await client.complete_json([], {}, max_tokens=5)


async def test_server_down_raises_and_waits_before_retry(monkeypatch) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    client = LlamaCppChat("http://llm:8080", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelUnavailableError):
        await client.complete_json([], {}, max_tokens=5)
    with pytest.raises(ModelUnavailableError):
        await client.complete_json([], {}, max_tokens=5)
    assert calls == 1  # the second call did not touch the server
    assert not await client.available()
    monkeypatch.setattr(llm_module, "RETRY_AFTER_SECONDS", 0.0)
    client._unavailable_until = 0.0
    with pytest.raises(ModelUnavailableError):
        await client.complete_json([], {}, max_tokens=5)
    assert calls == 2


async def test_http_error_is_unavailable() -> None:
    transport, _ = _server("", status=500)
    client = LlamaCppChat("http://llm:8080", transport=transport)
    with pytest.raises(ModelUnavailableError):
        await client.complete_text([], max_tokens=5)


async def test_health_ok() -> None:
    transport, _ = _server("")
    assert await LlamaCppChat("http://llm:8080", transport=transport).available()


def test_parse_json_object_tolerates_fences_and_prose() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Вот ответ: {"a": 1}.') == {"a": 1}
    with pytest.raises(ModelOutputError):
        parse_json_object("[1, 2]")
    with pytest.raises(ModelOutputError):
        parse_json_object("нет json")


# --- ALLOW_EXTERNAL_AI ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://llm-dialog:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "http://10.0.0.5:8080",
        "http://172.20.0.3:8080",
        "http://192.168.1.10:8080",
        "http://gpu-node.local:8000",
        "http://gpu.internal:8000",
    ],
)
def test_local_ai_addresses_are_allowed(url: str) -> None:
    settings = Settings(**BASE_ENV, llm_dialog_url=url, allow_external_ai=False)
    settings.check_external_ai()  # no exception
    assert settings.external_ai_hosts() == {}


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("llm_dialog_url", "https://api.openai.com/v1"),
        ("llm_gen_url", "https://llm.example.com:8443"),
        ("stt_url", "http://8.8.8.8:9000"),
    ],
)
def test_external_ai_address_refuses_to_start(field: str, url: str) -> None:
    settings = Settings(**BASE_ENV, allow_external_ai=False, **{field: url})
    with pytest.raises(RuntimeError, match="ALLOW_EXTERNAL_AI=false") as exc:
        settings.check_external_ai()
    assert field.upper() in str(exc.value)


def test_external_ai_address_allowed_only_with_the_flag() -> None:
    settings = Settings(
        **BASE_ENV, allow_external_ai=True, llm_dialog_url="https://llm.example.com"
    )
    settings.check_external_ai()
    assert settings.external_ai_hosts() == {"LLM_DIALOG_URL": "llm.example.com"}


def test_no_ai_addresses_is_fine() -> None:
    settings = Settings(**BASE_ENV, allow_external_ai=False)
    settings.check_external_ai()
    assert settings.ai_service_urls() == {}
