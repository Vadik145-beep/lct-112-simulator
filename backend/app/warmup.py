"""Loads the slow local models in the background at startup, so the first evaluation of a
call does not wait for them. On Docker Desktop with the models on a bind mount the e5 ONNX
session takes minutes to open; done once here instead of inside a trainee's «сохранить»."""

from __future__ import annotations

import asyncio
import time

from app.logging import get_logger

log = get_logger(__name__)


def _load() -> None:
    from app.providers.embeddings import get_embedding_provider
    from app.providers.tts import get_tts_provider

    started = time.perf_counter()
    embeddings = get_embedding_provider()
    embeddings.similarity("проверка", "проверка")
    # Piper voices: the opening line is voiced the moment the operator answers (PRD 9.5).
    tts = get_tts_provider()
    voices = tts.warm() if hasattr(tts, "warm") else 0
    log.info(
        "warmup done",
        embeddings=embeddings.method,
        voices=voices,
        seconds=round(time.perf_counter() - started, 1),
    )


def start() -> asyncio.Task[None]:
    """Fire and forget; a failure is logged and the providers fall back on first use."""

    async def run() -> None:
        try:
            await asyncio.get_running_loop().run_in_executor(None, _load)
        except Exception as exc:  # the lazy path handles a broken model on first use
            log.warning("warmup failed", error=str(exc))

    return asyncio.create_task(run())
