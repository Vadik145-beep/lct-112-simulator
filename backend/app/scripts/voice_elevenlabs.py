"""Voices the replies of call scenarios with ElevenLabs into ``data/seed/audio/<key>/``.

    python -m app.scripts.voice_elevenlabs --dry-run        # count the characters first
    python -m app.scripts.voice_elevenlabs                  # everything not voiced yet
    python -m app.scripts.voice_elevenlabs --only call_8-3  # one scenario

The layout is the one ``app.seed._attach_audio`` reads: ``opening.mp3``, ``r<id>.mp3`` and an
``index.json`` with the text every file was made from. A scenario whose folder already holds a
recording of the same text is skipped, so an interrupted run resumes where it stopped and the
studio voicing of the reference scenarios (model ``eleven_v3``, three formulations per reply) is
never overwritten.

The caller's voice slot (``caller.voice``) picks the ElevenLabs voice and its settings — the
casting is ``CAST`` below. The key of the API and nothing else comes from ``.env.elevenlabs``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_DIR / "data" / "seed" / "scenarios"
AUDIO_DIR = REPO_DIR / "data" / "seed" / "audio"
ENV_FILE = REPO_DIR / ".env.elevenlabs"

# eleven_flash_v2_5 takes the language explicitly, so short Russian phrases keep the accent;
# the studio scenarios use eleven_v3 with emotion tags, which this script does not write.
MODEL = "eleven_flash_v2_5"
API = "https://api.elevenlabs.io/v1/text-to-speech"


@dataclass(frozen=True)
class Voice:
    name: str
    voice_id: str
    speed: float
    stability: float


CAST: dict[str, Voice] = {
    "ru_female_1": Voice("Alina", "p6vKV7V6zDIyRuF9f39P", 1.0, 0.5),
    "ru_female_2": Voice("Anna", "q22qntNqQAvTaBoqE089", 0.85, 0.6),
    "ru_female_3": Voice("Lesia", "hfQ4EBmlnMg0YB8G0ItC", 1.1, 0.35),
    "ru_male_1": Voice("Vladimir", "4R4ha50vLnNdjMQDY20a", 1.0, 0.5),
    "ru_male_2": Voice("Nikolay", "3EuKHIEZbSzrHGNmdYsx", 1.05, 0.4),
    "ru_male_3": Voice("Alex", "9AjtU6o19uipv7QL8dLL", 0.85, 0.6),
    "ru_male_4": Voice("Vladislav", "drOVtcvHhJKNEXv5h1l5", 1.1, 0.4),
}


def api_key() -> str:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"нет ELEVENLABS_API_KEY в {ENV_FILE}")


def synthesize(key: str, voice: Voice, text: str) -> bytes:
    body = json.dumps(
        {
            "text": text,
            "model_id": MODEL,
            "language_code": "ru",
            "voice_settings": {
                "stability": voice.stability,
                "similarity_boost": 0.75,
                "speed": voice.speed,
            },
        }
    ).encode()
    req = urllib.request.Request(
        f"{API}/{voice.voice_id}",
        data=body,
        headers={"xi-api-key": key, "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=180).read()
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise SystemExit(f"ElevenLabs {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}")
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise SystemExit("не удалось получить аудио")


def studio_keys() -> set[str]:
    """Scenario keys already voiced in the studio — their folders may live only in another branch."""
    keys = {p.name for p in AUDIO_DIR.iterdir()} if AUDIO_DIR.exists() else set()
    try:
        out = subprocess.run(
            ["git", "ls-tree", "--name-only", "origin/main", "data/seed/audio/"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        keys |= {Path(line).name for line in out.stdout.split() if line.strip()}
    except (OSError, subprocess.SubprocessError):
        pass
    return keys


def phrases(body: dict) -> list[tuple[str, str]]:
    """(file stem, text) of everything the caller says: the opening and the approved replies."""
    items = [("opening", body["caller"]["opening"])]
    items += [
        (f"r{r['id']}", r["text"]) for r in body.get("replies", []) if r.get("approved") and r.get("text")
    ]
    return items


def run(only: str | None, dry_run: bool, limit: int | None) -> int:
    files = sorted(SCENARIOS_DIR.glob("call_*.json"))
    if only:
        files = [f for f in files if f.stem.startswith(only)]
    if not files:
        print("Сценарии не найдены.")
        return 2

    key = "" if dry_run else api_key()
    studio = studio_keys()
    made = skipped = chars = 0
    for path in files:
        if path.stem in studio and not only:
            skipped += 1
            continue
        body = json.loads(path.read_text(encoding="utf-8"))
        slot = body["caller"]["voice"]
        voice = CAST.get(slot)
        if voice is None:
            print(f"{path.stem}: нет голоса для {slot}, пропуск")
            continue
        folder = AUDIO_DIR / path.stem
        index_file = folder / "index.json"
        index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
        todo = [
            (stem, text)
            for stem, text in phrases(body)
            if not ((folder / f"{stem}.mp3").exists() and (index.get(stem) or {}).get("text") == text)
        ]
        if not todo:
            skipped += len(phrases(body))
            continue
        print(f"{path.stem} [{voice.name}]: {len(todo)} записей, {sum(len(t) for _, t in todo)} симв.")
        if dry_run:
            made += len(todo)
            chars += sum(len(t) for _, t in todo)
            continue
        folder.mkdir(parents=True, exist_ok=True)
        for stem, text in todo:
            audio = synthesize(key, voice, text)
            (folder / f"{stem}.mp3").write_bytes(audio)
            index[stem] = {
                "voice_id": voice.voice_id,
                "model": MODEL,
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
        if limit is not None and made >= limit:
            print(f"Достигнут предел {limit} записей, остановка.")
            break
    verb = "к озвучке" if dry_run else "озвучено"
    print(f"\nЗаписей {verb}: {made}, символов: {chars}, уже было: {skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default=None, help="начало имени сценария")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать символы")
    parser.add_argument("--limit", type=int, default=None, help="остановиться после N записей")
    args = parser.parse_args(argv)
    return run(args.only, args.dry_run, args.limit)


if __name__ == "__main__":
    sys.exit(main())
