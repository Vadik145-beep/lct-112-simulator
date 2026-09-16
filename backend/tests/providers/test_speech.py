"""STTProvider (HTTP client to the stt service) and TTSProvider (Piper) with their fallbacks."""

from __future__ import annotations

import os
import wave
from pathlib import Path

import httpx
import pytest

from app.providers.stt import COMMON_TERMS, HttpSTT, NoSTT, build_stt_provider, hint_prompt
from app.providers.tts import (
    VOICES,
    AudioClip,
    NoTTS,
    PiperTTS,
    build_tts_provider,
    iter_pcm_frames,
    mix_noise,
    noise_level_db,
    save_clip,
    split_sentences,
)

MODELS_DIR = Path(os.environ.get("MODELS_DIR", Path(__file__).resolve().parents[3] / "models"))
WAV_HEADER = b"RIFF\x00\x00\x00\x00WAVEfmt "  # enough to look like audio for the mock


# --- STT ---------------------------------------------------------------------------------------


def _stt_server(reply: dict | None, status: int = 200) -> tuple[httpx.MockTransport, list]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/audio/transcriptions"
        return httpx.Response(status, json=reply or {})

    return httpx.MockTransport(handler), seen


async def test_stt_sends_audio_with_hint_prompt() -> None:
    transport, seen = _stt_server(
        {"text": " Улица Берзарина, дом двадцать один ", "duration": 2.5, "processing_ms": 300}
    )
    stt = HttpSTT("http://stt:9000", transport=transport)
    result = await stt.transcribe(WAV_HEADER, "op.wav", hints=["улица Берзарина"])
    assert result.available
    assert result.text == "Улица Берзарина, дом двадцать один"
    assert result.duration_seconds == 2.5
    assert result.processing_ms == 300
    body = seen[0].content
    assert b'name="file"; filename="op.wav"' in body
    assert "улица Берзарина".encode() in body
    assert "подъезд".encode() in body  # common vocabulary follows the scenario terms


async def test_stt_down_falls_back_and_waits() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("refused")

    stt = HttpSTT("http://stt:9000", transport=httpx.MockTransport(handler))
    first = await stt.transcribe(WAV_HEADER)
    assert not first.available
    assert first.text == ""
    second = await stt.transcribe(WAV_HEADER)
    assert not second.available
    assert calls == 1


async def test_stt_empty_audio_is_not_sent() -> None:
    transport, seen = _stt_server({"text": "x"})
    result = await HttpSTT("http://stt:9000", transport=transport).transcribe(b"")
    assert result.text == ""
    assert seen == []


async def test_no_stt_means_typing() -> None:
    result = await NoSTT().transcribe(WAV_HEADER)
    assert not result.available
    assert build_stt_provider(None).method == "unavailable"
    assert build_stt_provider("http://stt:9000").method == "whisper"


def test_hint_prompt_dedupes_and_caps() -> None:
    prompt = hint_prompt(["Берзарина", "берзарина", "Волжский"])
    assert prompt.count("ерзарина") == 1
    assert prompt.startswith("Оператор 112 уточняет: Берзарина, Волжский")
    assert COMMON_TERMS[0] in prompt
    long = hint_prompt([f"улица {i}" for i in range(100)])
    assert long.count(",") < 45


# --- TTS ---------------------------------------------------------------------------------------


def test_split_sentences() -> None:
    assert split_sentences("Нет, никто. Соседке плохо стало! Она вышла… Да?") == [
        "Нет, никто.",
        "Соседке плохо стало!",
        "Она вышла…",
        "Да?",
    ]
    assert split_sentences("  ") == []


def test_noise_level_grows_with_difficulty() -> None:
    assert noise_level_db(None, 1) is None
    assert noise_level_db("silence", 1) is None
    assert noise_level_db("indoor", 1) == -36.0
    assert noise_level_db("indoor", 3) == -30.0
    assert noise_level_db("street", 2) > noise_level_db("indoor", 2)


def test_mix_noise_is_reproducible_and_louder_on_hard_scenarios() -> None:
    import numpy as np

    silence = AudioClip(pcm=b"\x00\x00" * 4000, sample_rate=8000)
    easy = mix_noise(silence, "indoor", 1)
    again = mix_noise(silence, "indoor", 1)
    hard = mix_noise(silence, "indoor", 3)
    assert easy.pcm == again.pcm
    rms = lambda c: float(np.sqrt(np.mean(np.frombuffer(c.pcm, np.int16).astype(float) ** 2)))  # noqa: E731
    assert rms(easy) > 0
    assert rms(hard) > rms(easy) * 1.9  # +6 dB from difficulty 1 to 3
    assert mix_noise(silence, None, 3).pcm == silence.pcm


def test_wav_bytes_and_frames() -> None:
    clip = AudioClip(pcm=b"\x01\x00" * 1600, sample_rate=16000)
    wav = clip.wav_bytes()
    assert wav.startswith(b"RIFF")
    assert clip.duration_seconds == 0.1
    frames = list(iter_pcm_frames(clip, frame_ms=20))
    assert len(frames) == 5
    assert all(len(f) == 640 for f in frames)


async def test_no_tts_returns_nothing() -> None:
    assert await NoTTS().synthesize("Алло") is None
    assert await NoTTS().synthesize_sentences("Алло. Да.") == []
    assert build_tts_provider(None).method == "text"
    assert build_tts_provider("/nonexistent").method == "text"


@pytest.mark.skipif(
    not (MODELS_DIR / "tts" / "ru_RU-denis-medium.onnx").exists(),
    reason="голоса Piper не скачаны (scripts/fetch_models.sh --only tts)",
)
async def test_piper_voices_reply(tmp_path: Path) -> None:
    provider = build_tts_provider(str(MODELS_DIR))
    assert isinstance(provider, PiperTTS)
    assert set(provider.available_voices()) >= {"ru_male_1", "ru_male_3"}
    clip = await provider.synthesize(
        "Вавилова, восемьдесят один, корпус один.", "ru_male_3", "indoor", 2
    )
    assert clip is not None
    assert 1.0 < clip.duration_seconds < 8.0
    assert clip.sample_rate == 22050
    paths = save_clip(clip, tmp_path / "r1")
    with wave.open(str(paths["wav"]), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 22050
    slow = await provider.synthesize("Вавилова, восемьдесят один, корпус один.", "ru_male_3")
    normal = await provider.synthesize("Вавилова, восемьдесят один, корпус один.", "ru_male_1")
    assert slow is not None and normal is not None
    assert slow.duration_seconds > normal.duration_seconds  # ru_male_3 is the slow elderly voice
    sentences = await provider.synthesize_sentences("Нет, никто. Соседке плохо стало.")
    assert len(sentences) == 2
    assert all(VOICES[v].model for v in provider.available_voices())


# --- embeddings (e5 through onnxruntime) ---------------------------------------------------------


@pytest.mark.skipif(
    not (MODELS_DIR / "embeddings" / "multilingual-e5-small" / "onnx" / "model.onnx").exists(),
    reason="модель e5 не скачана (scripts/fetch_models.sh --only embeddings)",
)
def test_e5_separates_paraphrases_from_unrelated_text() -> None:
    from app.providers.embeddings import build_embedding_provider

    provider = build_embedding_provider(str(MODELS_DIR / "embeddings" / "multilingual-e5-small"))
    assert provider.method == "e5-small"
    low, high = provider.thresholds
    same = provider.similarity(
        "мусоропровод дымит, пламени нет", "задымление мусоропровода без огня"
    )
    other = provider.similarity("мусоропровод дымит, пламени нет", "отравление лекарствами")
    assert same >= high
    assert other < low
    vectors = provider.embed(["запах газа", "пахнет газом"])
    assert len(vectors) == 2 and len(vectors[0]) == 384
