"""TTSProvider: the caller's voice.

Main implementation: Silero TTS v4 (PyTorch, CPU, ``models/tts/silero_v4_ru.pt``): five native
Russian speakers, each at its own pace and pitch (SSML prosody stays available but neutral:
shifted voices sounded artificial). Piper (ONNX, three Russian voices) stays as the second
engine (``TTS_PROVIDER=piper``) and as the fallback when the Silero model or torch is missing.
A scenario names a voice as ``ru_male_1``, ``ru_female_1``…; ``VOICES`` lists those ids (shown
in the teacher's form), ``SILERO_VOICES`` / ``VoiceSpec`` map them to an engine voice, so the
same scenario sounds the same on every stand. Background noise (``caller.noise``: indoor, street,
crowd) is mixed in at a level that grows with the scenario difficulty, so a harder call is harder
to hear (PRD 9.3).

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
from typing import Literal, Protocol
from xml.sax.saxutils import escape

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

TTSEngine = Literal["auto", "silero", "piper", "text"]
SILERO_MODEL_FILE = "silero_v4_ru.pt"  # models/tts, scripts/models.manifest
SILERO_SAMPLE_RATE = 24000  # the model offers 8, 24 and 48 kHz
SILERO_THREADS = 2  # torch intra-op threads: keeps the API and the worker responsive


@dataclass(frozen=True)
class SileroVoiceSpec:
    speaker: str  # aidar, baya, kseniya, xenia, eugene
    rate: str = "medium"  # SSML prosody rate: x-slow … x-fast
    pitch: str = "medium"  # SSML prosody pitch: x-low … x-high


# Scenario voice ids → Silero speakers at their own pace and pitch. The slowed, lowered and
# raised variants of 22.09.2026 sounded artificial on the stand and were dropped (25.09.2026):
# the three female ids get the three female speakers, the child the lightest of them, and the
# two male speakers serve the four male ids.
SILERO_VOICES: dict[str, SileroVoiceSpec] = {
    "ru_male_1": SileroVoiceSpec("aidar"),
    "ru_male_2": SileroVoiceSpec("eugene"),
    "ru_male_3": SileroVoiceSpec("eugene"),
    "ru_male_4": SileroVoiceSpec("aidar"),
    "ru_female_1": SileroVoiceSpec("kseniya"),
    "ru_female_2": SileroVoiceSpec("baya"),
    "ru_female_3": SileroVoiceSpec("xenia"),
    "ru_child_1": SileroVoiceSpec("xenia"),
}
assert set(SILERO_VOICES) == set(VOICES)

_DIGITS = re.compile(r"\d+")
_LONG_NUMBER = 5  # digit runs this long and longer (phones, card numbers) are read digit by digit

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

    def warm(self) -> int:
        """Loads every voice found on disk (slow on a bind mount) so the first reply of a
        call does not wait for it; returns how many voices are ready. Blocking: call it
        from a worker thread."""
        models = {VOICES[vid].model for vid in self.available_voices()}
        for model in sorted(models):
            self._load(model)
        return len(models)

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


class SileroTTS:
    """Silero TTS v4 (``silero_v4_ru.pt``, torch on CPU). The model reads no digits, so numbers
    are spelled out first (``spell_numbers``); the voice id picks a speaker and SSML prosody."""

    method = "silero"

    def __init__(self, model_path: Path) -> None:
        self._path = model_path
        self._model = None
        self._lock = asyncio.Lock()

    def available_voices(self) -> list[str]:
        return list(SILERO_VOICES) if self._path.exists() else []

    def warm(self) -> int:
        """Loads the model (a few seconds, see ``_load``) so the opening line of a call does not
        wait; returns the number of voices ready. Blocking: call it from a worker thread."""
        self._load()
        return len(SILERO_VOICES)

    def _load(self):
        if self._model is None:
            import torch

            if not self._path.exists():
                raise FileNotFoundError(f"модель Silero не найдена: {self._path}")
            torch.set_num_threads(SILERO_THREADS)
            # The legacy TorchScript executor: the profiling one spends the first two
            # syntheses (seconds each) on profiling; with it off only the first is slow.
            torch._C._jit_set_profiling_executor(False)
            torch._C._jit_set_profiling_mode(False)
            # A file object, not the path: torch's C++ reader trips over non-ASCII folders
            # on Windows (a developer checkout under a Cyrillic path).
            with self._path.open("rb") as file:
                model = torch.package.PackageImporter(file).load_pickle("tts_models", "model")
            model.to(torch.device("cpu"))
            self._model = model
            # The one slow synthesis is paid here, once, not inside somebody's call.
            self._synthesize_sync("Алло.", SILERO_VOICES[DEFAULT_VOICE])
        return self._model

    def _synthesize_sync(self, text: str, spec: SileroVoiceSpec) -> AudioClip:
        model = self._load()
        ssml = (
            f'<speak><prosody rate="{spec.rate}" pitch="{spec.pitch}">'
            f"{escape(spell_numbers(text))}</prosody></speak>"
        )
        audio = model.apply_tts(
            ssml_text=ssml, speaker=spec.speaker, sample_rate=SILERO_SAMPLE_RATE
        )
        pcm = (audio.clamp(-1.0, 1.0) * 32767).to("cpu").numpy().astype("<i2").tobytes()
        return AudioClip(pcm=pcm, sample_rate=SILERO_SAMPLE_RATE)

    async def synthesize(
        self, text: str, voice: str | None = None, noise: str | None = None, difficulty: int = 1
    ) -> AudioClip | None:
        text = text.strip()
        if not text:
            return None
        spec = SILERO_VOICES.get(voice or DEFAULT_VOICE, SILERO_VOICES[DEFAULT_VOICE])
        async with self._lock:  # one torch model, one synthesis at a time
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


def spell_numbers(text: str) -> str:
    """``11 лет`` → ``одиннадцать лет``; a phone number is read digit by digit. Silero skips
    digits it cannot read; Piper (espeak) reads them itself, so only Silero calls this."""
    from num2words import num2words

    def replace(match: re.Match[str]) -> str:
        digits = match.group(0)
        if len(digits) >= _LONG_NUMBER:
            return " ".join(num2words(int(d), lang="ru") for d in digits)
        return num2words(int(digits), lang="ru")

    return _DIGITS.sub(replace, text)


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


def _build_silero(voices_dir: Path) -> TTSProvider | None:
    provider = SileroTTS(voices_dir / SILERO_MODEL_FILE)
    if not provider.available_voices():
        log.warning("silero model not found", path=str(provider._path))
        return None
    try:
        import num2words  # noqa: F401 - digits are spelled out before synthesis
        import torch  # noqa: F401 - checks the library is installed
    except Exception as exc:  # broken install: try the next engine, do not crash
        log.warning("silero unavailable", error=str(exc))
        return None
    log.info("tts provider", method=provider.method, voices=provider.available_voices())
    return provider


def _build_piper(voices_dir: Path) -> TTSProvider | None:
    try:
        provider = PiperTTS(voices_dir)
        voices = provider.available_voices()
        if voices:
            import piper  # noqa: F401 - checks the library is installed

            log.info("tts provider", method=provider.method, voices=voices)
            return provider
        log.warning("no Piper voices found", dir=str(voices_dir))
    except Exception as exc:  # broken install: text only, do not crash
        log.warning("piper unavailable", error=str(exc))
    return None


def build_tts_provider(models_dir: str | None, engine: TTSEngine = "auto") -> TTSProvider:
    """``auto``: Silero when its model and torch are there, else Piper, else text only. An
    explicit engine that is not available also degrades to text (with a warning)."""
    if models_dir and engine != "text":
        voices_dir = Path(models_dir) / "tts"
        if engine in ("auto", "silero"):
            provider = _build_silero(voices_dir)
            if provider is not None:
                return provider
        if engine in ("auto", "piper"):
            provider = _build_piper(voices_dir)
            if provider is not None:
                return provider
        log.warning("no tts engine available, replies will be text only", engine=engine)
    return NoTTS()


_provider: TTSProvider | None = None


def get_tts_provider() -> TTSProvider:
    global _provider
    if _provider is None:
        from app.config import get_settings

        settings = get_settings()
        _provider = build_tts_provider(settings.models_dir, settings.tts_provider)
    return _provider
