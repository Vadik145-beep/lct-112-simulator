"""Universal replies of the caller for a question no topic of the scenario covers.

``data/seed/fallback_phrases.json`` holds ten phrases in a male and a female wording; every
caller voice has them recorded in ``data/seed/audio/_fallback/<voice>/`` (``app.seed`` copies
those to ``STORAGE_DIR/tts/seed/_fallback/…``). The dialog takes one when the scenario has no
approved ``repeat`` / ``unknown`` reply left, so the caller answers in their own voice instead
of a line synthesized on the spot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_FILE = Path(__file__).resolve().parents[4] / "data" / "seed" / "fallback_phrases.json"
AUDIO_PREFIX = "tts/seed/_fallback"


@dataclass(frozen=True)
class FallbackPhrase:
    id: str
    topic: str  # repeat | unknown
    male: str
    female: str

    def text(self, voice: str) -> str:
        return self.female if "female" in voice else self.male

    def audio(self, voice: str) -> str:
        return f"{AUDIO_PREFIX}/{voice}/{self.id}.mp3"


@lru_cache(maxsize=1)
def phrases() -> tuple[FallbackPhrase, ...]:
    if not DATA_FILE.exists():  # a deployment without the seed data
        return ()
    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return tuple(
        FallbackPhrase(id=p["id"], topic=p["topic"], male=p["male"], female=p["female"])
        for p in raw.get("phrases", [])
    )


def pick(topic: str, voice: str, said: set[str]) -> FallbackPhrase | None:
    """A phrase of the topic, preferring one the caller has not said in this call yet."""
    candidates = [p for p in phrases() if p.topic == topic]
    if not candidates:
        return None
    unused = [p for p in candidates if p.text(voice) not in said]
    return (unused or candidates)[0]
