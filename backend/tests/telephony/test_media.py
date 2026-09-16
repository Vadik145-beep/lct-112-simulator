"""RTP parsing, the phrase segmenter and the audio helpers of the call pipeline."""

from __future__ import annotations

import math
import struct
import wave
from io import BytesIO

import numpy as np
import pytest

from app.telephony import media


def rtp_packet(
    seq: int,
    timestamp: int,
    payload: bytes,
    *,
    csrc: int = 0,
    ext: bytes | None = None,
    padding: int = 0,
) -> bytes:
    first = 0x80 | (0x10 if ext is not None else 0) | (0x20 if padding else 0) | csrc
    header = struct.pack("!BBHII", first, 118, seq, timestamp, 0xABCD) + b"\0\0\0\0" * csrc
    if ext is not None:
        header += struct.pack("!HH", 0xBEDE, len(ext) // 4) + ext
    body = payload + (bytes(padding - 1) + bytes([padding]) if padding else b"")
    return header + body


def tone(
    seconds: float, freq: float = 300.0, amplitude: int = 12000, rate: int = media.SAMPLE_RATE
) -> bytes:
    n = int(seconds * rate)
    samples = (amplitude * np.sin(2 * math.pi * freq * np.arange(n) / rate)).astype("<i2")
    return samples.tobytes()


def silence(seconds: float, rate: int = media.SAMPLE_RATE) -> bytes:
    return bytes(2 * int(seconds * rate))


class TestRtp:
    def test_plain_packet(self):
        seq, ts, payload = media.parse_rtp(rtp_packet(7, 320, b"\x01\x02\x03\x04"))
        assert (seq, ts, payload) == (7, 320, b"\x01\x02\x03\x04")

    def test_csrc_extension_and_padding_are_skipped(self):
        packet = rtp_packet(1, 0, b"\x10\x20", csrc=2, ext=b"\0\0\0\0", padding=3)
        assert media.parse_rtp(packet)[2] == b"\x10\x20"

    def test_garbage_is_rejected(self):
        assert media.parse_rtp(b"\x00" * 5) is None
        assert media.parse_rtp(b"\x40" + b"\x00" * 20) is None  # version 1

    def test_network_order_roundtrip(self):
        pcm = tone(0.02)
        assert media.rtp_payload_to_pcm(media.pcm_to_rtp_payload(pcm)) == pcm
        assert media.pcm_to_rtp_payload(b"\x01\x02") == b"\x02\x01"


class TestSegmenter:
    def make(self) -> media.Segmenter:
        return media.Segmenter(media.EnergyVad(), silence_ms=400, min_phrase_ms=200)

    def feed_all(self, segmenter: media.Segmenter, pcm: bytes) -> list[bytes]:
        phrases = []
        for frame in media.iter_frames(pcm):
            phrases.extend(segmenter.feed(frame))
        return phrases

    def test_speech_between_silences_becomes_one_phrase(self):
        segmenter = self.make()
        phrases = self.feed_all(segmenter, silence(0.5) + tone(1.0) + silence(0.8))
        assert len(phrases) == 1
        seconds = len(phrases[0]) / (media.SAMPLE_RATE * 2)
        # The phrase carries the tone plus the pre-roll and the closing silence.
        assert 1.2 <= seconds <= 2.0

    def test_short_blip_is_ignored(self):
        segmenter = self.make()
        assert self.feed_all(segmenter, silence(0.3) + tone(0.1) + silence(0.8)) == []

    def test_two_phrases(self):
        segmenter = self.make()
        pcm = silence(0.3) + tone(0.6) + silence(0.6) + tone(0.5) + silence(0.6)
        assert len(self.feed_all(segmenter, pcm)) == 2

    def test_flush_returns_phrase_in_progress(self):
        segmenter = self.make()
        assert self.feed_all(segmenter, silence(0.3) + tone(0.6)) == []
        assert segmenter.flush() is not None

    def test_long_speech_is_cut_at_max(self):
        segmenter = media.Segmenter(media.EnergyVad(), max_phrase_ms=1000, silence_ms=400)
        phrases = self.feed_all(segmenter, tone(2.5) + silence(0.6))
        assert len(phrases) >= 2


class TestHelpers:
    def test_pcm_to_wav(self):
        pcm = tone(0.1)
        with wave.open(BytesIO(media.pcm_to_wav(pcm)), "rb") as wav:
            assert wav.getframerate() == media.SAMPLE_RATE
            assert wav.getnchannels() == 1
            assert wav.readframes(wav.getnframes()) == pcm

    def test_resample_changes_length(self):
        pcm = tone(0.5, rate=22050)
        out = media.resample_pcm(pcm, 22050, 16000)
        assert abs(len(out) / 2 - 8000) <= 2

    def test_port_pool(self):
        pool = media.PortPool(12000, 12001)
        a, b = pool.take(), pool.take()
        assert {a, b} == {12000, 12001}
        with pytest.raises(RuntimeError):
            pool.take()
        pool.give_back(a)
        assert pool.take() == a

    def test_build_vad_without_model_is_energy(self):
        assert isinstance(media.build_vad(None), media.EnergyVad)
        assert isinstance(media.build_vad("/no/such/file.onnx"), media.EnergyVad)
