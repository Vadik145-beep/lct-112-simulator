"""GrammarProvider: spelling and grammar check of dispatcher comments.

Main implementation talks to a local LanguageTool server (compose service ``languagetool``).
When it is down the provider answers «не проверено» (``method="unavailable"``) instead of
failing, and the evaluation renormalizes the grammar component (PRD section 6). The server is
probed again after ``RETRY_AFTER_SECONDS`` so a restarted LanguageTool is picked up without
restarting the API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.config import get_settings
from app.logging import get_logger

log = get_logger(__name__)

DEFAULT_LANGUAGE = "ru-RU"
REQUEST_TIMEOUT_SECONDS = 5.0
RETRY_AFTER_SECONDS = 30.0
MAX_TEXT_LENGTH = 5000


@dataclass
class GrammarMatch:
    offset: int
    length: int
    message: str
    rule_id: str
    category: str
    replacements: list[str] = field(default_factory=list)


@dataclass
class GrammarResult:
    matches: list[GrammarMatch]
    method: str  # "languagetool" | "unavailable"

    @property
    def available(self) -> bool:
        return self.method != "unavailable"

    @property
    def error_count(self) -> int:
        return len(self.matches)


class GrammarProvider(Protocol):
    method: str

    async def check(self, text: str, language: str = DEFAULT_LANGUAGE) -> GrammarResult: ...


class NoGrammar:
    """Fallback without a model: nothing is checked, the caller renormalizes."""

    method = "unavailable"

    async def check(self, text: str, language: str = DEFAULT_LANGUAGE) -> GrammarResult:
        return GrammarResult([], self.method)


class LanguageToolGrammar:
    method = "languagetool"

    def __init__(
        self,
        base_url: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v2/check"
        self._timeout = timeout
        self._transport = transport  # tests inject a mock transport
        self._unavailable_until = 0.0

    async def check(self, text: str, language: str = DEFAULT_LANGUAGE) -> GrammarResult:
        text = text[:MAX_TEXT_LENGTH]
        if not text.strip():
            return GrammarResult([], self.method)
        if time.monotonic() < self._unavailable_until:
            return GrammarResult([], "unavailable")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(self._url, data={"text": text, "language": language})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._unavailable_until = time.monotonic() + RETRY_AFTER_SECONDS
            log.warning("languagetool unavailable", error=str(exc))
            return GrammarResult([], "unavailable")
        matches = [
            GrammarMatch(
                offset=m["offset"],
                length=m["length"],
                message=m.get("message", ""),
                rule_id=m.get("rule", {}).get("id", ""),
                category=m.get("rule", {}).get("category", {}).get("id", ""),
                replacements=[r["value"] for r in m.get("replacements", [])][:5],
            )
            for m in payload.get("matches", [])
        ]
        return GrammarResult(matches, self.method)


_provider: GrammarProvider | None = None


def get_grammar_provider() -> GrammarProvider:
    global _provider
    if _provider is None:
        url = get_settings().languagetool_url
        _provider = LanguageToolGrammar(url) if url else NoGrammar()
    return _provider
