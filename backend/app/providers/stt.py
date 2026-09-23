"""STTProvider: the operator's voice → text.

Main implementation talks to the ``stt`` service of the ``ai`` profile (``deploy/stt``,
faster-whisper) through the OpenAI-compatible transcription endpoint, so the GPU node of track G
can replace it without touching the backend. Every request carries a hint prompt with the
streets and terms of the scenario: Whisper hears rare street names far better when it has seen
them spelled (PRD 6, plan wave 5).

Fallback without a model: ``NoSTT`` — the operator types instead of speaking.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from app.logging import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 60.0
RETRY_AFTER_SECONDS = 15.0
DEFAULT_LANGUAGE = "ru"
# Whisper reads at most ~224 tokens of the initial prompt; keep the hint short.
MAX_HINT_TERMS = 40

# Words of the trade the recognizer should know regardless of the scenario.
COMMON_TERMS: tuple[str, ...] = (
    "служба сто двенадцать",
    "подъезд",
    "домофон",
    "корпус",
    "строение",
    "пострадавшие",
    "задымление",
    "возгорание",
    "запах газа",
    "скорая",
    "полиция",
    "пожарные",
    "МЧС",
    "ДПС",
    "перезвонить",
    # Как диспетчер спрашивает: короткую фразу распознаватель без контекста слышит криво
    # («имя заявителя» → «нима заявителя»), а в подсказке она узнаётся.
    "имя заявителя",
    "фамилия",
    "адрес происшествия",
    "телефон для связи",
    "есть пострадавшие",
    "что случилось",
)


@dataclass
class Transcript:
    text: str
    method: str  # "whisper" | "unavailable"
    duration_seconds: float = 0.0
    processing_ms: int = 0
    segments: list[dict] = field(default_factory=list)
    # Peak amplitude of the recording (0..1, -1 unknown): a silent microphone shows as ~0.
    peak: float = -1.0

    @property
    def available(self) -> bool:
        return self.method != "unavailable"


class STTProvider(Protocol):
    method: str

    async def transcribe(
        self, audio: bytes, filename: str = "audio.wav", hints: Iterable[str] = ()
    ) -> Transcript: ...


class NoSTT:
    """Fallback without a model: nothing is recognized, the operator types."""

    method = "unavailable"

    async def transcribe(
        self, audio: bytes, filename: str = "audio.wav", hints: Iterable[str] = ()
    ) -> Transcript:
        return Transcript("", self.method)


class HttpSTT:
    method = "whisper"

    def __init__(
        self,
        base_url: str,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/v1/audio/transcriptions"
        self._timeout = timeout
        self._transport = transport  # tests inject a mock transport
        self._unavailable_until = 0.0

    async def transcribe(
        self, audio: bytes, filename: str = "audio.wav", hints: Iterable[str] = ()
    ) -> Transcript:
        if not audio:
            return Transcript("", self.method)
        if time.monotonic() < self._unavailable_until:
            return Transcript("", "unavailable")
        data = {"language": DEFAULT_LANGUAGE, "prompt": hint_prompt(hints)}
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._url, data=data, files={"file": (filename, audio)}
                )
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self._unavailable_until = time.monotonic() + RETRY_AFTER_SECONDS
            log.warning("stt unavailable", error=str(exc))
            return Transcript("", "unavailable")
        return Transcript(
            text=str(payload.get("text") or "").strip(),
            method=self.method,
            duration_seconds=float(payload.get("duration") or 0.0),
            peak=float(payload.get("peak", -1.0)),
            processing_ms=int(payload.get("processing_ms") or 0),
            segments=list(payload.get("segments") or []),
        )


def hint_prompt(hints: Iterable[str]) -> str:
    """Initial prompt for Whisper: scenario terms first, then the common vocabulary.

    Written as a plausible sentence fragment, because Whisper treats the prompt as preceding
    speech rather than a word list.
    """
    seen: list[str] = []
    for term in [*hints, *COMMON_TERMS]:
        term = term.strip()
        if term and term.lower() not in {s.lower() for s in seen}:
            seen.append(term)
        if len(seen) >= MAX_HINT_TERMS:
            break
    return "Оператор 112 уточняет: " + ", ".join(seen) + "."


def build_stt_provider(stt_url: str | None) -> STTProvider:
    if stt_url:
        log.info("stt provider", method="whisper", url=stt_url)
        return HttpSTT(stt_url)
    return NoSTT()


_provider: STTProvider | None = None


def get_stt_provider() -> STTProvider:
    global _provider
    if _provider is None:
        from app.config import get_settings

        _provider = build_stt_provider(get_settings().stt_url)
    return _provider
