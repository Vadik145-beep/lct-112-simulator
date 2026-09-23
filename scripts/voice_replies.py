"""Studio voicing of the reference call-intake scenarios through ElevenLabs (eleven_v3).

The ``select`` dialog mode plays approved replies verbatim, so their voice can be rendered
once, ahead of time, with emotion tags the on-stand Piper cannot do. The result is data: one
MP3 per opening, per approved reply and per its other wording (``variants``) under
``data/seed/audio/<seed key>/``, which
``app.seed`` copies into ``storage/tts/seed/`` and links from the scenario body. Scenarios
made later on the stand (generated or edited) keep the Piper path.

Runs on a machine with internet, never on the stand. ``index.json`` in every folder holds a
hash of (voice, model, tag, text) per file, so a rerun voices only what changed.

Usage (repository root; key and voice ids in ``.env.elevenlabs``, see ``--help``):
    python scripts/voice_replies.py            # voice everything missing or changed
    python scripts/voice_replies.py --dry-run  # only count characters
    python scripts/voice_replies.py --only call_2-1_zadymlenie_musoroprovoda --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_DIR = ROOT / "data" / "seed" / "scenarios"
AUDIO_DIR = ROOT / "data" / "seed" / "audio"
ENV_FILE = ROOT / ".env.elevenlabs"

API = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = "mp3_44100_128"

# Scenario voice ids (app.providers.tts.VOICES) → env variable with the ElevenLabs voice.
# Age and tempo of a persona come from the emotion tag, not from a separate voice.
VOICE_ENV: dict[str, str] = {
    "ru_male_1": "ELEVENLABS_VOICE_MALE",
    "ru_male_2": "ELEVENLABS_VOICE_MALE",
    "ru_male_3": "ELEVENLABS_VOICE_MALE",
    "ru_male_4": "ELEVENLABS_VOICE_MALE",
    "ru_female_1": "ELEVENLABS_VOICE_FEMALE_1",
    "ru_female_2": "ELEVENLABS_VOICE_FEMALE_1",
    "ru_female_3": "ELEVENLABS_VOICE_FEMALE_2",
    "ru_child_1": "ELEVENLABS_VOICE_FEMALE_2",
}

# eleven_v3 audio tags by persona (app.domain.scenarios.personas plus the codes the seed
# scenarios use). The tag goes in front of the phrase; the model reads it, not says it.
PERSONA_TAGS: dict[str, str] = {
    "calm": "[calm]",
    "calm_commuter": "[calm]",
    "worried_resident": "[nervous]",
    "elderly_calm": "[calm] [slowly]",
    "elderly_panicked": "[panicked] [shaky voice]",
    "mother_anxious": "[anxious] [fast]",
    "witness_shaken": "[shaky voice] [nervous]",
    "witness_urgent": "[urgent] [fast]",
    "victim_panicked": "[panicked] [in pain]",
    "angry_customer": "[angry] [irritated]",
    "child": "[nervous]",
    "drunk": "[slurred]",
}
DEFAULT_TAG = "[nervous]"
# Service replies («repeat again», «I don't know») sound the same whatever the persona.
TOPIC_TAGS: dict[str, str] = {"repeat": "[shouting]"}


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    values.update({k: v for k, v in os.environ.items() if k.startswith("ELEVENLABS_")})
    return values


def digest(voice_id: str, tag: str, text: str) -> str:
    return hashlib.sha256(f"{voice_id}|{MODEL_ID}|{tag}|{text}".encode()).hexdigest()[:40]


def synthesize(client: httpx.Client, voice_id: str, text: str) -> bytes:
    body = {
        "text": text,
        "model_id": MODEL_ID,
        "language_code": "ru",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.8},
    }
    for attempt in range(3):
        response = client.post(
            f"/text-to-speech/{voice_id}", params={"output_format": OUTPUT_FORMAT}, json=body
        )
        if response.status_code == 429 and attempt < 2:
            time.sleep(5 * (attempt + 1))
            continue
        if response.status_code != 200:
            raise SystemExit(f"ElevenLabs {response.status_code}: {response.text[:300]}")
        return response.content
    raise SystemExit("ElevenLabs: too many retries")


def remaining_characters(client: httpx.Client) -> tuple[int, int] | None:
    try:
        data = client.get("/user/subscription").raise_for_status().json()
    except (httpx.HTTPError, ValueError):
        return None
    return int(data.get("character_count", 0)), int(data.get("character_limit", 0))


def phrases(body: dict) -> list[tuple[str, str, str]]:
    """(file stem, tag, text) for the opening and every approved reply."""
    caller = body.get("caller") or {}
    persona_tag = PERSONA_TAGS.get(caller.get("persona", ""), DEFAULT_TAG)
    items: list[tuple[str, str, str]] = []
    opening = (caller.get("opening") or "").strip()
    if opening:
        items.append(("opening", persona_tag, opening))
    for reply in body.get("replies") or []:
        text = (reply.get("text") or "").strip()
        if not text or not reply.get("approved"):
            continue
        tag = TOPIC_TAGS.get(reply.get("topic", ""), persona_tag)
        items.append((f"r{int(reply['id'])}", tag, text))
        for n, variant in enumerate(reply.get("variants") or [], start=1):
            other = (variant.get("text") or "").strip()
            if other:
                items.append((f"r{int(reply['id'])}-v{n}", tag, other))
    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--only", action="append", default=[], help="seed key (file stem), repeatable"
    )
    parser.add_argument("--force", action="store_true", help="re-render even if unchanged")
    parser.add_argument("--dry-run", action="store_true", help="count characters, call nothing")
    args = parser.parse_args()

    env = load_env(ENV_FILE)
    key = env.get("ELEVENLABS_API_KEY", "")
    if not key and not args.dry_run:
        raise SystemExit(f"ELEVENLABS_API_KEY не задан ({ENV_FILE})")

    files = sorted(SCENARIOS_DIR.glob("*.json"))
    if args.only:
        files = [f for f in files if f.stem in set(args.only)]
    todo: list[tuple[str, str, str, str, str]] = []  # key, stem, voice_id, tag, text
    skipped = 0
    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        if body.get("kind") != "call_intake":
            continue
        voice = (body.get("caller") or {}).get("voice") or "ru_male_1"
        voice_id = env.get(VOICE_ENV.get(voice, "ELEVENLABS_VOICE_MALE"), "")
        if not voice_id:
            raise SystemExit(f"{path.stem}: нет ElevenLabs-голоса для {voice} в {ENV_FILE}")
        folder = AUDIO_DIR / path.stem
        index_path = folder / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        for stem, tag, text in phrases(body):
            current = index.get(stem, {})
            if (
                not args.force
                and current.get("sha256") == digest(voice_id, tag, text)
                and (folder / f"{stem}.mp3").exists()
            ):
                skipped += 1
                continue
            todo.append((path.stem, stem, voice_id, tag, text))

    chars = sum(len(f"{tag} {text}") for _, _, _, tag, text in todo)
    print(f"озвучить: {len(todo)} фраз, {chars} символов; без изменений: {skipped}")
    if args.dry_run or not todo:
        return
    client = httpx.Client(base_url=API, headers={"xi-api-key": key}, timeout=120)
    quota = remaining_characters(client)
    if quota:
        used, limit = quota
        print(f"квота: использовано {used} из {limit}, останется ~{limit - used - chars}")

    done = 0
    for scenario_key, stem, voice_id, tag, text in todo:
        folder = AUDIO_DIR / scenario_key
        folder.mkdir(parents=True, exist_ok=True)
        index_path = folder / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        audio = synthesize(client, voice_id, f"{tag} {text}")
        (folder / f"{stem}.mp3").write_bytes(audio)
        index[stem] = {
            "sha256": digest(voice_id, tag, text),
            "voice_id": voice_id,
            "model": MODEL_ID,
            "tag": tag,
            "text": text,
            "bytes": len(audio),
        }
        index_path.write_text(
            json.dumps(dict(sorted(index.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        done += 1
        print(
            f"[{done}/{len(todo)}] {scenario_key}/{stem}.mp3 {len(audio) // 1024} КБ"
            f"  {tag} {text[:50]}"
        )
    print("готово")


if __name__ == "__main__":
    sys.exit(main())
