"""Quick check of speech recognition per STT server: the same operator questions are
synthesized with Piper (a voice no scenario uses) and sent to every candidate; prints what each
one heard and the latency, so the base/small choice is made on the stand's own CPU.

Usage (repository root, backend venv, MODELS_DIR pointing at the models):
    uv run --project backend python scripts/bench_stt.py \\
        --stt base=http://127.0.0.1:9000 --stt small=http://127.0.0.1:9001
"""

from __future__ import annotations

import argparse
import asyncio
import io
import statistics
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.providers.stt import HttpSTT  # noqa: E402
from app.providers.tts import get_tts_provider  # noqa: E402

QUESTIONS = [
    "Служба 112, слушаю вас. Что случилось?",
    "Назовите адрес: улица, дом.",
    "Вы один дома или с кем-то?",
    "Сколько у вас комнат в квартире?",
    "Кот или собака дома есть?",
    "Как вас зовут?",
    "Телефон для связи?",
    "Есть пострадавшие, кому-то нужна помощь?",
    "Подъезд, этаж, код домофона?",
    "Газом пахнет или дымом?",
]


def to_wav(pcm: bytes, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(pcm)
    return buffer.getvalue()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stt", action="append", required=True, help="имя=url")
    parser.add_argument("--voice", default="ru_female_1", help="голос Piper для вопросов оператора")
    args = parser.parse_args()
    tts = get_tts_provider()
    clips = []
    for q in QUESTIONS:
        clip = await tts.synthesize(q, voice=args.voice)
        assert clip is not None, "Piper недоступен: проверьте MODELS_DIR"
        clips.append(to_wav(clip.pcm, clip.sample_rate))
    for spec in args.stt:
        name, url = spec.split("=", 1)
        stt = HttpSTT(url, timeout=120)
        print(f"== {name} ({url})")
        latencies: list[float] = []
        for q, audio in zip(QUESTIONS, clips, strict=True):
            started = time.perf_counter()
            heard = await stt.transcribe(audio, "q.wav")
            seconds = time.perf_counter() - started
            latencies.append(seconds)
            exact = heard.text.strip().lower().rstrip("?.!") == q.lower().rstrip("?.!")
            mark = "✔" if exact else " "
            print(f"   {seconds:4.1f}s {mark} сказано: {q}")
            print(f"           услышано: {heard.text}")
        print(f"   медиана {statistics.median(latencies):.1f} с, максимум {max(latencies):.1f} с")


if __name__ == "__main__":
    asyncio.run(main())
