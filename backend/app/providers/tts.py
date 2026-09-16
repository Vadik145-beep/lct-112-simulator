"""TTSProvider: the caller's voice.

Main implementation: Piper (ONNX, CPU) with the Russian voices from ``models/tts``
(``scripts/fetch_models.sh``). A scenario names a voice as ``ru_male_1``, ``ru_female_1``…;
``VOICES`` maps those to a Piper model plus speaking rate, so the same scenario sounds the same on
every stand. Background noise (``caller.noise``: indoor, street, crowd) is mixed in at a level
that grows with the scenario difficulty, so a harder call is harder to hear (PRD 9.3).

Replies are voiced once, when the teacher approves them (wave 8), and stored as WAV plus MP3
(MP3 needs ``ffmpeg``; without it only WAV is written). ``generate`` mode voices sentence by
sentence so the first sound comes out before the whole phrase is ready.

Fallback without a model: ``NoTTS`` — the reply is shown as text, nothing is played.
"""

from __future__ import annotations

import asyncio
import io
import re
import shutil
import subprocess
import wave
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.logging import get_logger

log = get_logger(__name__)

SAMPLE_WIDTH = 2  # 16-bit PCM
MP3_BITRATE = "64k"
FFMPEG_TIMEOUT_SECONDS = 60
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class VoiceSpec:
    model: str  # Piper model file stem in models/tts
    length_scale: float = 1.0  # > 1 slower speech
    description: str = ""


# Scenario voice ids → Piper voices. denis and dmitri are CC0, irina's dataset licence is not
# stated by the authors (docs/LIBRARIES.md); all three are the only Russian Piper voices without
# a non-commercial clause on the model itself.
VOICES: dict[str, VoiceSpec] = {
    "ru_male_1": VoiceSpec("ru_RU-denis-medium", 1.0, "мужской, обычный темп"),
    "ru_male_2": VoiceSpec("ru_RU-dmitri-medium", 1.0, "мужской, обычный темп"),
    "ru_male_3": VoiceSpec("ru_RU-denis-medium", 1.2, "мужской, медленный (пожилой)"),
    "ru_male_4": VoiceSpec("ru_RU-dmitri-medium", 0.85, "мужской, быстрый (взволнованный)"),
    "ru_female_1": VoiceSpec("ru_RU-irina-medium", 1.0, "женский, обычный темп"),
    "ru_female_2": VoiceSpec("ru_RU-irina-medium", 1.2, "женский, медленный (пожилая)"),
    "ru_female_3": VoiceSpec("ru_RU-irina-medium", 0.85, "женский, быстрый (паника)"),
    "ru_child_1": VoiceSpec("ru_RU-irina-medium", 0.9, "детский голос приближённо"),
}
DEFAULT_VOICE = "ru_male_1"

# Noise level in dB relative to full scale at difficulty 1; every further level adds NOISE_STEP_DB.
NOISE_LEVELS_DB: dict[str, float] = {"indoor": -36.0, "street": -28.0, "crowd": -24.0}
NOISE_STEP_DB = 3.0


@dataclass
class AudioClip:
    pcm: bytes  # 16-bit mono PCM
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        return len(self.pcm) / SAMPLE_WIDTH / self.sample_rate

    def wav_bytes(self) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(SAMPLE_WIDTH)
            out.setframerate(self.sample_rate)
            out.writeframes(self.pcm)
        return buffer.getvalue()


class TTSProvider(Protocol):
    method: str

    async def synthesize(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> AudioClip | None: ...

    async def synthesize_sentences(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> list[AudioClip]: ...


class NoTTS:
    """Fallback without a model: text only, nothing to play."""

    method = "text"

    async def synthesize(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> AudioClip | None:
        return None

    async def synthesize_sentences(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> list[AudioClip]:
        return []


class PiperTTS:
    method = "piper"

    def __init__(self, voices_dir: Path) -> None:
        self._dir = voices_dir
        self._voices: dict[str, object] = {}
        self._lock = asyncio.Lock()

    def available_voices(self) -> list[str]:
        return [vid for vid, spec in VOICES.items() if (self._dir / f"{spec.model}.onnx").exists()]

    def _load(self, model: str):  # PiperVoice, imported lazily
        from piper import PiperVoice

        if model not in self._voices:
            path = self._dir / f"{model}.onnx"
            if not path.exists():
                raise FileNotFoundError(f"голос Piper не найден: {path}")
            self._voices[model] = PiperVoice.load(str(path))
        return self._voices[model]

    def _synthesize_sync(self, text: str, spec: VoiceSpec) -> AudioClip:
        from piper import SynthesisConfig

        voice = self._load(spec.model)
        config = SynthesisConfig(length_scale=spec.length_scale)
        chunks = list(voice.synthesize(text, syn_config=config))
        pcm = b"".join(chunk.audio_int16_bytes for chunk in chunks)
        rate = chunks[0].sample_rate if chunks else voice.config.sample_rate
        return AudioClip(pcm=pcm, sample_rate=rate)

    async def synthesize(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> AudioClip | None:
        text = text.strip()
        if not text:
            return None
        spec = VOICES.get(voice or DEFAULT_VOICE, VOICES[DEFAULT_VOICE])
        async with self._lock:  # one ONNX session per voice; keep the calls sequential
            clip = await asyncio.to_thread(self._synthesize_sync, text, spec)
        return mix_noise(clip, noise, difficulty)

    async def synthesize_sentences(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> list[AudioClip]:
        clips = []
        for sentence in split_sentences(text):
            clip = await self.synthesize(sentence, voice, noise, difficulty)
            if clip is not None:
                clips.append(clip)
        return clips


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s]


def noise_level_db(noise: str | None, difficulty: int) -> float | None:
    if not noise or noise not in NOISE_LEVELS_DB:
        return None
    return NOISE_LEVELS_DB[noise] + NOISE_STEP_DB * (max(difficulty, 1) - 1)


def mix_noise(clip: AudioClip, noise: str | None, difficulty: int, seed: int = 7) -> AudioClip:
    """Add coloured (low-pass filtered) noise at the level of the scenario difficulty.

    The generator is seeded so the same reply always gets the same audio, which keeps the
    stored files reproducible.
    """
    level_db = noise_level_db(noise, difficulty)
    if level_db is None or not clip.pcm:
        return clip
    import numpy as np

    rng = np.random.default_rng(seed)
    samples = np.frombuffer(clip.pcm, dtype=np.int16).astype(np.float32) / 32768.0
    white = rng.standard_normal(len(samples)).astype(np.float32)
    # A moving average low-passes the hiss so it sounds like a room or street rumble.
    kernel = 12 if noise == "indoor" else 4
    coloured = np.convolve(white, np.ones(kernel, dtype=np.float32) / kernel, mode="same")
    coloured /= max(float(np.abs(coloured).max()), 1e-6)
    gain = 10 ** (level_db / 20)
    mixed = np.clip(samples + coloured * gain, -1.0, 1.0)
    return AudioClip(pcm=(mixed * 32767).astype(np.int16).tobytes(), sample_rate=clip.sample_rate)


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def encode_mp3(wav: bytes) -> bytes | None:
    """MP3 through ffmpeg; ``None`` when ffmpeg is not installed."""
    binary = ffmpeg_path()
    if binary is None:
        return None
    command = [
        binary,
        "-loglevel",
        "error",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        MP3_BITRATE,
        "-f",
        "mp3",
        "pipe:1",
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed binary and arguments, input from memory
            command, input=wav, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=True
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("mp3 encoding failed", error=str(exc))
        return None
    return result.stdout


def save_clip(clip: AudioClip, stem: Path) -> dict[str, Path]:
    """Write ``<stem>.wav`` and, when ffmpeg is available, ``<stem>.mp3``."""
    stem.parent.mkdir(parents=True, exist_ok=True)
    wav = clip.wav_bytes()
    paths = {"wav": stem.with_suffix(".wav")}
    paths["wav"].write_bytes(wav)
    mp3 = encode_mp3(wav)
    if mp3 is not None:
        paths["mp3"] = stem.with_suffix(".mp3")
        paths["mp3"].write_bytes(mp3)
    return paths


def iter_pcm_frames(clip: AudioClip, frame_ms: int = 20) -> Iterator[bytes]:
    """PCM frames of ``frame_ms`` for streaming to the telephony node (wave 6)."""
    step = clip.sample_rate * frame_ms // 1000 * SAMPLE_WIDTH
    for start in range(0, len(clip.pcm), step):
        yield clip.pcm[start : start + step]


def build_tts_provider(models_dir: str | None) -> TTSProvider:
    """Piper when the voices folder has at least one voice and the library imports."""
    if models_dir:
        voices_dir = Path(models_dir) / "tts"
        try:
            provider = PiperTTS(voices_dir)
            voices = provider.available_voices()
            if voices:
                import piper  # noqa: F401 - checks the library is installed

                log.info("tts provider", method=provider.method, voices=voices)
                return provider
            log.warning("no Piper voices found, replies will be text only", dir=str(voices_dir))
        except Exception as exc:  # broken install: text only, do not crash
            log.warning("piper unavailable, replies will be text only", error=str(exc))
    return NoTTS()


_provider: TTSProvider | None = None


def get_tts_provider() -> TTSProvider:
    global _provider
    if _provider is None:
        from app.config import get_settings

        _provider = build_tts_provider(get_settings().models_dir)
    return _provider
