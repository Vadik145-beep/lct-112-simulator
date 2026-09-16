"""A scripted ChatModel: answers from a queue, records what it was asked."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from app.providers.llm import (
    Message,
    ModelOutputError,
    ModelUnavailableError,
    parse_json_object,
)


class FakeModel:
    name = "fake"

    def __init__(self, answers: Sequence[str | dict[str, Any] | Exception] = ()) -> None:
        self.answers: list[str | dict[str, Any] | Exception] = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.is_available = True

    def push(self, *answers: str | dict[str, Any] | Exception) -> None:
        self.answers.extend(answers)

    async def available(self) -> bool:
        return self.is_available

    def _next(self, kind: str, messages: Sequence[Message], **kwargs: Any) -> str:
        self.calls.append({"kind": kind, "messages": list(messages), **kwargs})
        if not self.answers:
            raise ModelUnavailableError("fake: нет заготовленных ответов")
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return json.dumps(answer, ensure_ascii=False) if isinstance(answer, dict) else answer

    async def complete_json(
        self,
        messages: Sequence[Message],
        schema: dict[str, Any],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        content = self._next(
            "json", messages, schema=schema, max_tokens=max_tokens, slot_key=slot_key
        )
        try:
            return parse_json_object(content)
        except ModelOutputError:
            raise

    async def complete_text(
        self,
        messages: Sequence[Message],
        *,
        max_tokens: int,
        slot_key: str | None = None,
        temperature: float = 0.0,
    ) -> str:
        return self._next("text", messages, max_tokens=max_tokens, slot_key=slot_key)
