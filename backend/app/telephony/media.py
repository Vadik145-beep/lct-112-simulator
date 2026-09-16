"""Audio of a call: the operator's voice arrives from Asterisk as RTP (ExternalMedia,
``slin16``: 16 kHz, 16-bit mono), is cut into phrases by a voice activity detector and goes to
speech recognition; the caller's replies are converted to Asterisk's raw ``sln16`` files for
playback (PRD 9.5).

The detector is Silero VAD (ONNX on CPU, ``models/vad/silero_vad.onnx``); without the file a
plain energy threshold does the job, less reliably in noise.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import struct
import subprocess
import wave
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from app.logging import get_logger

log = get_logger(__name__)

SAMPLE_RATE = 16000  # slin16
SAMPLE_WIDTH = 2
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * SAMPLE_WIDTH
RTP_HEADER_BYTES = 12
# Asterisk sends slin over RTP in network byte order (big-endian) as RFC 3551 L16 asks.
RTP_PCM_DTYPE = ">i2"
FFMPEG_TIMEOUT_SECONDS = 60
ASTERISK_SOUND_SUFFIX = ".sln16"


# ---------------------------------------------------------------- RTP


def parse_rtp(packet: bytes) -> tuple[int, int, bytes] | None:
    """``(sequence, timestamp, payload)`` of an RTP packet, ``None`` for garbage."""
    if len(packet) < RTP_HEADER_BYTES:
        return None
    first = packet[0]
    if first >> 6 != 2:  # version
        return None
    csrc_count = first & 0x0F
    has_extension = bool(first & 0x10)
    has_padding = bool(first & 0x20)
    offset = RTP_HEADER_BYTES + 4 * csrc_count
    if has_extension:
        if len(packet) < offset + 4:
            return None
        ext_words = struct.unpack("!H", packet[offset + 2 : offset + 4])[0]
        offset += 4 + 4 * ext_words
    if len(packet) < offset:
        return None
    sequence, timestamp = struct.unpack("!HI", packet[2:8])
    payload = packet[offset:]
    if has_padding and payload:
        payload = payload[: -payload[-1]]
    return sequence, timestamp, payload


def rtp_payload_to_pcm(payload: bytes) -> bytes:
    """Network-order L16 → little-endian 16-bit PCM used everywhere else."""
    if len(payload) % SAMPLE_WIDTH:
        payload = payload[: len(payload) - 1]
    return np.frombuffer(payload, dtype=RTP_PCM_DTYPE).astype("<i2").tobytes()


def pcm_to_rtp_payload(pcm: bytes) -> bytes:
    return np.frombuffer(pcm, dtype="<i2").astype(">i2").tobytes()


class RtpReceiver(asyncio.DatagramProtocol):
    """UDP socket for one call: RTP in, PCM frames out through ``frames``. The address of the
    first sender is kept so ``send`` can answer on the same flow."""

    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None
        self.peer: tuple[str, int] | None = None
        self.packets = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        parsed = parse_rtp(data)
        if parsed is None:
            return
        if self.peer is None:
            self.peer = addr
        self.packets += 1
        self.frames.put_nowait(rtp_payload_to_pcm(parsed[2]))

    def error_received(self, exc: Exception) -> None:
        log.warning("rtp socket error", error=str(exc))

    def connection_lost(self, exc: Exception | None) -> None:
        self.frames.put_nowait(None)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


async def open_receiver(host: str, port: int) -> RtpReceiver:
    loop = asyncio.get_running_loop()
    _, protocol = await loop.create_datagram_endpoint(RtpReceiver, local_addr=(host, port))
    return protocol


class PortPool:
    """UDP ports for ExternalMedia, one per call."""

    def __init__(self, start: int, end: int) -> None:
        self._free = deque(range(start, end + 1))

    def take(self) -> int:
        if not self._free:
            raise RuntimeError("свободных портов для звука не осталось")
        return self._free.popleft()

    def give_back(self, port: int) -> None:
        self._free.append(port)


# ---------------------------------------------------------------- voice activity


class SileroVad:
    """Silero VAD v5 through onnxruntime: 512-sample windows at 16 kHz, a probability each."""

    WINDOW = 512

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), options, providers=["CPUExecutionProvider"]
        )
        self.reset()

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def probability(self, window: np.ndarray) -> float:
        out, self._state = self._session.run(
            None,
            {
                "input": window.reshape(1, -1).astype(np.float32),
                "state": self._state,
                "sr": np.array(SAMPLE_RATE, dtype=np.int64),
            },
        )
        return float(out[0][0])


class EnergyVad:
    """Fallback without the model: RMS above a threshold counts as speech."""

    WINDOW = 512
    THRESHOLD = 0.02

    def reset(self) -> None:
        return None

    def probability(self, window: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(window * window))) if len(window) else 0.0
        return 1.0 if rms > self.THRESHOLD else 0.0


def build_vad(model_path: str | None) -> SileroVad | EnergyVad:
    if model_path and Path(model_path).exists():
        try:
            vad = SileroVad(Path(model_path))
            log.info("vad", method="silero", path=model_path)
            return vad
        except Exception as exc:  # broken file or runtime: still answer calls
            log.warning("silero vad unavailable, using energy detector", error=str(exc))
    else:
        log.info("vad", method="energy")
    return EnergyVad()


@dataclass
class Segmenter:
    """Cuts a PCM stream into phrases. Speech starts after ``start_windows`` voiced windows,
    ends after ``silence_ms`` of silence; ``pre_roll_ms`` before the start are kept so the
    first syllable is not lost. ``feed`` returns the finished phrases."""

    vad: SileroVad | EnergyVad
    speech_threshold: float = 0.5
    silence_threshold: float = 0.35
    start_windows: int = 2
    silence_ms: int = 600
    pre_roll_ms: int = 300
    min_phrase_ms: int = 350
    max_phrase_ms: int = 15000
    _buffer: bytes = b""
    _pre_roll: deque = field(default_factory=deque)
    _phrase: bytearray = field(default_factory=bytearray)
    _voiced_run: int = 0
    _silence_run_ms: int = 0
    _in_speech: bool = False
    _pre_roll_bytes: int = 0

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, pcm: bytes) -> list[bytes]:
        self._buffer += pcm
        window_bytes = self.vad.WINDOW * SAMPLE_WIDTH
        window_ms = self.vad.WINDOW * 1000 // SAMPLE_RATE
        phrases: list[bytes] = []
        while len(self._buffer) >= window_bytes:
            chunk = self._buffer[:window_bytes]
            self._buffer = self._buffer[window_bytes:]
            samples = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            probability = self.vad.probability(samples)
            if not self._in_speech:
                self._pre_roll.append(chunk)
                keep = max(1, self.pre_roll_ms // window_ms)
                while len(self._pre_roll) > keep:
                    self._pre_roll.popleft()
                if probability >= self.speech_threshold:
                    self._voiced_run += 1
                    if self._voiced_run >= self.start_windows:
                        self._in_speech = True
                        self._phrase = bytearray(b"".join(self._pre_roll))
                        self._pre_roll_bytes = len(self._phrase)
                        self._pre_roll.clear()
                        self._silence_run_ms = 0
                else:
                    self._voiced_run = 0
                continue
            self._phrase += chunk
            if probability < self.silence_threshold:
                self._silence_run_ms += window_ms
            else:
                self._silence_run_ms = 0
            phrase_ms = len(self._phrase) * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH)
            if self._silence_run_ms >= self.silence_ms or phrase_ms >= self.max_phrase_ms:
                self._in_speech = False
                self._voiced_run = 0
                pre_roll_ms = self._pre_roll_bytes * 1000 // (SAMPLE_RATE * SAMPLE_WIDTH)
                speech_ms = phrase_ms - pre_roll_ms - self._silence_run_ms
                if speech_ms >= self.min_phrase_ms:
                    phrases.append(bytes(self._phrase))
                self._phrase = bytearray()
                self.vad.reset()
        return phrases

    def flush(self) -> bytes | None:
        """The phrase in progress when the stream ends."""
        speech_bytes = len(self._phrase) - self._pre_roll_bytes
        if self._in_speech and speech_bytes >= self.min_phrase_ms * SAMPLE_RATE * 2 // 1000:
            phrase = bytes(self._phrase)
            self._phrase = bytearray()
            self._in_speech = False
            return phrase
        return None


# ---------------------------------------------------------------- files


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(sample_rate)
        out.writeframes(pcm)
    return buffer.getvalue()


def iter_frames(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    for start in range(0, len(pcm), frame_bytes):
        frame = pcm[start : start + frame_bytes]
        if len(frame) < frame_bytes:
            frame += b"\0" * (frame_bytes - len(frame))
        yield frame


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def to_asterisk_sound(source: Path) -> Path | None:
    """``<source>.sln16`` (raw 16 kHz mono PCM) next to the source file; ``None`` when ffmpeg
    is missing or fails. The result is cached: Asterisk plays it directly."""
    target = source.with_suffix(ASTERISK_SOUND_SUFFIX)
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return target
    binary = ffmpeg_path()
    if binary is None:
        log.warning("ffmpeg missing, cannot prepare sound for asterisk", source=str(source))
        return None
    command = [
        binary,
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "s16le",
        str(target),
    ]
    try:
        subprocess.run(  # noqa: S603 - fixed binary, paths from our storage
            command, capture_output=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=True
        )
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("sound conversion failed", source=str(source), error=str(exc))
        return None
    return target


def resample_pcm(pcm: bytes, from_rate: int, to_rate: int = SAMPLE_RATE) -> bytes:
    """Linear resampling of 16-bit mono PCM (good enough for speech to the recognizer)."""
    if from_rate == to_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    length = int(len(samples) * to_rate / from_rate)
    positions = np.linspace(0, len(samples) - 1, num=length)
    resampled = np.interp(positions, np.arange(len(samples)), samples)
    return np.clip(resampled, -32768, 32767).astype("<i2").tobytes()
