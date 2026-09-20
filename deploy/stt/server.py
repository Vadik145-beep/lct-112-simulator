"""Speech-to-text service: faster-whisper (CTranslate2, CPU, int8).

One endpoint mirrors OpenAI's ``POST /v1/audio/transcriptions`` (multipart ``file``, optional
``language``, ``prompt``, ``temperature``) and answers ``{"text": …, "segments": […],
"duration": …}``. ``prompt`` is passed to Whisper as the initial prompt: the backend puts the
streets and terms of the scenario there so «Берзарина» is not heard as «Березина».

The model folder comes from ``STT_MODEL_DIR`` (filled by scripts/fetch_models.sh); the service
never downloads anything.
"""

from __future__ import annotations

import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio

MODEL_DIR = Path(os.environ.get("STT_MODEL_DIR", "/models/stt/faster-whisper-base"))
COMPUTE_TYPE = os.environ.get("STT_COMPUTE_TYPE", "int8")
THREADS = int(os.environ.get("STT_THREADS", "4"))
DEFAULT_LANGUAGE = "ru"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Diagnostics: a folder to keep uploads that produced no text (empty = keep nothing).
DUMP_DIR = os.environ.get("STT_DUMP_DIR", "")

model: WhisperModel | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global model
    if not MODEL_DIR.exists():
        raise RuntimeError(
            f"Папка модели {MODEL_DIR} не найдена: выполните scripts/fetch_models.sh --only stt"
        )
    model = WhisperModel(str(MODEL_DIR), device="cpu", compute_type=COMPUTE_TYPE, cpu_threads=THREADS)
    yield


app = FastAPI(title="STT (faster-whisper)", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": MODEL_DIR.name, "compute_type": COMPUTE_TYPE}


@app.post("/v1/audio/transcriptions")
async def transcribe(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency style
    language: str = Form(DEFAULT_LANGUAGE),
    prompt: str | None = Form(None),
    temperature: float = Form(0.0),
) -> dict:
    assert model is not None
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "Пустой файл")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Файл больше 25 МБ")
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    started = time.perf_counter()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(payload)
        path = tmp.name
    text = ""
    try:
        # Level of the recording: tells a silent microphone from unrecognised speech.
        try:
            samples = decode_audio(path, sampling_rate=16000)
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2))) if samples.size else 0.0
        except Exception:  # noqa: BLE001 - a level is a bonus, the transcription still runs
            peak, rms = -1.0, -1.0
        segments, info = model.transcribe(
            path,
            language=language,
            initial_prompt=prompt or None,
            temperature=temperature,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        parts = [
            {"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()}
            for s in segments
        ]
        text = " ".join(p["text"] for p in parts).strip()
    finally:
        if DUMP_DIR and not text:
            try:
                Path(DUMP_DIR).mkdir(parents=True, exist_ok=True)
                Path(DUMP_DIR, f"{int(time.time())}{suffix}").write_bytes(payload)
            except OSError:
                pass
        os.unlink(path)
    return {
        "text": text,
        "segments": parts,
        "language": info.language,
        "duration": round(info.duration, 2),
        "peak": round(peak, 4),
        "rms": round(rms, 4),
        "processing_ms": round((time.perf_counter() - started) * 1000),
    }
