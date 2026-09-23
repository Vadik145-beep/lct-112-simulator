"""Voices the universal replies of ``data/seed/fallback_phrases.json`` with every caller voice.

    python -m app.scripts.voice_fallback --dry-run
    python -m app.scripts.voice_fallback

One recording per phrase and voice into ``data/seed/audio/_fallback/<voice slot>/<id>.mp3``,
with an ``index.json`` beside them, the same bookkeeping as ``app.scripts.voice_elevenlabs``.
These answer an operator's question that no topic of the scenario covers, so the caller keeps
their voice instead of falling back to a locally synthesized line.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from app.scripts.voice_elevenlabs import AUDIO_DIR, CAST, MODEL, REPO_DIR, api_key, synthesize

PHRASES_FILE = REPO_DIR / "data" / "seed" / "fallback_phrases.json"
FALLBACK_KEY = "_fallback"

# The bank must sound like the caller of that voice usually sounds, or the general phrase would
# stand out against the scenario's own replies.
VOICE_TAGS = {
    "ru_female_1": "[calm]",
    "ru_female_2": "[calm] [slowly]",
    "ru_female_3": "[shaky voice] [nervous]",
    "ru_male_1": "[calm]",
    "ru_male_2": "[angry] [irritated]",
    "ru_male_3": "[calm] [slowly]",
    "ru_male_4": "[anxious] [fast]",
}


def run(dry_run: bool) -> int:
    phrases = json.loads(PHRASES_FILE.read_text(encoding="utf-8"))["phrases"]
    key = "" if dry_run else api_key()
    made = skipped = chars = 0
    for slot, voice in CAST.items():
        folder = AUDIO_DIR / FALLBACK_KEY / slot
        index_file = folder / "index.json"
        index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
        gender = "female" if "female" in slot else "male"
        tag = VOICE_TAGS.get(slot, "[calm]")
        todo = [
            (p["id"], p[gender])
            for p in phrases
            if not (
                (folder / f"{p['id']}.mp3").exists()
                and (index.get(p["id"]) or {}).get("text") == p[gender]
                and (index.get(p["id"]) or {}).get("model") == MODEL
            )
        ]
        if not todo:
            skipped += len(phrases)
            continue
        print(f"{slot} [{voice.name}]: {len(todo)} записей, {sum(len(t) for _, t in todo)} симв.")
        if dry_run:
            made += len(todo)
            chars += sum(len(t) for _, t in todo)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        for phrase_id, text in todo:
            audio = synthesize(key, voice, text, model=MODEL, tag=tag)
            (folder / f"{phrase_id}.mp3").write_bytes(audio)
            index[phrase_id] = {
                "voice_id": voice.voice_id,
                "model": MODEL,
                "tag": tag,
                "speed": voice.speed,
                "stability": voice.stability,
                "text": text,
                "bytes": len(audio),
                "sha256": hashlib.sha256(audio).hexdigest(),
            }
            made += 1
            chars += len(text)
        index_file.write_text(
            json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    verb = "к озвучке" if dry_run else "озвучено"
    print(f"\nЗаписей {verb}: {made}, символов: {chars}, уже было: {skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="только посчитать символы")
    args = parser.parse_args(argv)
    return run(args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
