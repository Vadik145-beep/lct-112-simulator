"""Chat model client: llama.cpp server behind the OpenAI-compatible chat endpoint.

Two servers of the ``ai`` compose profile use it: ``llm-dialog`` (the caller in a training call)
and ``llm-gen`` (scenario generation, wave 8; the judge in the latency benchmark). The client asks
for JSON constrained by a schema (llama.cpp builds a grammar from it, so the model cannot answer
outside the schema) and keeps the system prompt cached per conversation: ``cache_prompt`` reuses
the KV cache of a slot and ``id_slot`` pins one conversation to one slot (PRD 9.3).

``OpenAiCompatibleChat`` is the same client for a hosted model (a key and a model name), used
for scenario generation on a demonstration stand outside the closed contour.

When the server is down the client raises ``ModelUnavailableError`` and does not retry for
``RETRY_AFTER_SECONDS``; the dialog provider then degrades to the mode without a model.
"""

from __future__ import annotations

import json
import time
import zlib
from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.logging import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 60.0
RETRY_AFTER_SECONDS = 15.0
DEFAULT_TEMPERATURE = 0.0

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}


class ModelUnavailableError(Exception):
    """The model server did not answer (down, timeout, HTTP error)."""


class ModelOutputError(Exception):
    """The model answered, but not with the JSON we asked for."""


class ChatModel(Protocol):
    name: str

    async def complete_json(
        self,
        messages: Sequence[Message],
        schema: dict[str, Any],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> dict[str, Any]: ...

    async def complete_text(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str: ...

    async def available(self) -> bool: ...


class LlamaCppChat:
    """llama.cpp server (``llama-server``) through ``/v1/chat/completions``."""

    def __init__(
        self,
        base_url: str,
        name: str = "llama.cpp",
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport  # tests inject a mock transport
        self._unavailable_until = 0.0
        self._total_slots: int | None = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base, timeout=self._timeout, transport=self._transport
        )

    async def available(self) -> bool:
        if time.monotonic() < self._unavailable_until:
            return False
        try:
            async with self._client() as client:
                response = await client.get("/health", timeout=3.0)
                return response.status_code == 200
        except httpx.HTTPError:
            self._unavailable_until = time.monotonic() + RETRY_AFTER_SECONDS
            return False

    async def _slot_for(self, client: httpx.AsyncClient, slot_key: str | None) -> int | None:
        """Pin a conversation to a slot so its cached system prompt is reused."""
        if slot_key is None:
            return None
        if self._total_slots is None:
            try:
                response = await client.get("/props", timeout=3.0)
                self._total_slots = int(response.json().get("total_slots") or 1)
            except (httpx.HTTPError, ValueError, TypeError):
                self._total_slots = 1
        return zlib.crc32(slot_key.encode()) % self._total_slots

    async def _chat(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        slot_key: str | None,
        temperature: float,
        extra: dict[str, Any],
    ) -> str:
        if time.monotonic() < self._unavailable_until:
            raise ModelUnavailableError(f"{self.name}: недавно не отвечал, повтор позже")
        payload: dict[str, Any] = {
            **self._payload(messages, max_tokens=max_tokens, temperature=temperature),
            **extra,
        }
        started = time.perf_counter()
        try:
            async with self._client() as client:
                slot = await self._slot_for(client, slot_key)
                if slot is not None:
                    payload["id_slot"] = slot
                response = await client.post("/v1/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
                content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._unavailable_until = time.monotonic() + RETRY_AFTER_SECONDS
            log.warning("chat model unavailable", model=self.name, error=str(exc))
            raise ModelUnavailableError(f"{self.name}: {exc}") from exc
        log.debug(
            "chat completion",
            model=self.name,
            ms=round((time.perf_counter() - started) * 1000),
            usage=body.get("usage"),
        )
        return content if isinstance(content, str) else ""

    async def complete_json(
        self,
        messages: Sequence[Message],
        schema: dict[str, Any],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> dict[str, Any]:
        content = await self._chat(
            messages,
            max_tokens=max_tokens,
            slot_key=slot_key,
            temperature=temperature,
            extra={"response_format": self._json_format(schema)},
        )
        return parse_json_object(content)

    def _payload(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        return {
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_prompt": True,
        }

    def _json_format(self, schema: dict[str, Any]) -> dict[str, Any]:
        # llama.cpp turns the schema into a grammar: the model cannot leave it.
        return {"type": "json_object", "schema": schema}

    async def complete_text(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
    ) -> str:
        return await self._chat(
            messages, max_tokens=max_tokens, slot_key=slot_key, temperature=temperature, extra={}
        )


class OpenAiCompatibleChat(LlamaCppChat):
    """A hosted model behind the OpenAI-compatible chat API (a key and a model name).

    The same endpoint as llama.cpp, so the base address is given without ``/v1``. Differences:
    the key goes in the ``Authorization`` header, the model is named in the request, the schema
    is passed as ``json_schema`` (structured outputs) and there are no slots or prompt cache
    of llama.cpp. Such services have no ``/health``: the model counts as available unless a
    request has just failed. Outside the closed contour only (``ALLOW_EXTERNAL_AI``).
    """

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str,
        model: str,
        name: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(base_url, name=name or model, timeout=timeout, transport=transport)
        self._api_key = api_key
        self._model = model

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base,
            timeout=self._timeout,
            transport=self._transport,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )

    async def available(self) -> bool:
        return time.monotonic() >= self._unavailable_until

    async def _slot_for(self, client: httpx.AsyncClient, slot_key: str | None) -> int | None:
        return None

    def _payload(
        self, messages: Sequence[Message], *, max_tokens: int, temperature: float
    ) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": list(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    def _json_format(self, schema: dict[str, Any]) -> dict[str, Any]:
        # Not strict: strict mode demands every property required, our schemas have optional
        # ones. The answer is validated by the caller anyway.
        return {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}


def parse_json_object(content: str) -> dict[str, Any]:
    """The model's answer as a JSON object; tolerant to a markdown fence around it."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ModelOutputError(f"в ответе модели нет JSON-объекта: {content[:80]!r}")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ModelOutputError(f"битый JSON от модели: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelOutputError("ответ модели не объект")
    return value
