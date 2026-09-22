"""``app.scripts.revoice``: re-voicing the stand after the TTS engine changed."""

from __future__ import annotations

import uuid

from app.scripts import revoice

SCENARIO = str(uuid.uuid4())


def test_studio_and_uploaded_recordings_are_kept() -> None:
    """A recording the stand did not synthesize wins over any local engine: the studio voicing
    of the reference scenarios and the teacher's own upload are never replaced."""
    uploaded = {(SCENARIO, 3)}
    studio = f"{revoice.SEED_AUDIO_PREFIX}call_2-1_zadymlenie_musoroprovoda/r1.mp3"
    assert revoice.keeps_recording(studio, SCENARIO, 1, uploaded)
    assert revoice.keeps_recording(f"tts/{SCENARIO}/v1/r3.mp3", SCENARIO, 3, uploaded)
    # Synthesized by the stand and not uploaded: this is what the run re-voices.
    assert not revoice.keeps_recording(f"tts/{SCENARIO}/v1/r1.mp3", SCENARIO, 1, uploaded)
    assert not revoice.keeps_recording(f"tts/{SCENARIO}/v1/r3.mp3", str(uuid.uuid4()), 3, uploaded)


async def test_without_a_voice_engine_the_script_says_so(capsys) -> None:
    class NoVoice:
        method = "text"

    original = revoice.get_tts_provider
    revoice.get_tts_provider = lambda: NoVoice()
    try:
        assert await revoice.run(None, dry_run=False) == 2
    finally:
        revoice.get_tts_provider = original
    assert "нечего озвучивать" in capsys.readouterr().out
