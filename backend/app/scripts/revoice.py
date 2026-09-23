"""Re-voices the approved replies of every call scenario with the current TTS engine.

    python -m app.scripts.revoice            # everything voiced by a synthesizer
    python -m app.scripts.revoice --dry-run  # only count
    python -m app.scripts.revoice --scenario <uuid>

Needed after the voice engine changes (Piper → Silero, 22.09.2026): ``voice_reply`` keeps a
reply's audio once it exists, so a stand keeps the old voice until the file is replaced.

Two kinds of recording are left alone, because they are not synthesized on the stand and sound
better than any local engine: the studio voicing of the reference scenarios (ElevenLabs,
``tts/seed/…``, see ``app.seed._attach_audio``) and a recording the teacher uploaded (audit
``scenario.reply.audio``). Only the latest version of a scenario is touched — earlier versions
are history. Runs inside the worker/backend container, where the models and STORAGE_DIR are
mounted.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CallIntakeScenario
from app.logging import configure_logging
from app.models import AuditLog, Scenario, ScenarioVersion
from app.providers.tts import get_tts_provider
from app.scenarios.service import ALLOWED_AUDIO, _replies, audio_stem

# Recordings the stand did not synthesize: the studio voicing of the reference scenarios that
# the seeder copies into ``STORAGE_DIR/tts/seed/<key>/`` (``app.seed``, ветка студийной озвучки).
SEED_AUDIO_PREFIX = "tts/seed/"


def keeps_recording(audio: str, scenario_id: str, reply_id: int, uploaded: set) -> bool:
    """A studio recording or one the teacher uploaded always wins over a local engine."""
    return audio.startswith(SEED_AUDIO_PREFIX) or (scenario_id, reply_id) in uploaded


async def uploaded_replies(session) -> set[tuple[str, int]]:
    """(scenario id, reply id) pairs whose audio the teacher uploaded."""
    rows = await session.execute(
        select(AuditLog.entity_id, AuditLog.details).where(
            AuditLog.action == "scenario.reply.audio"
        )
    )
    pairs: set[tuple[str, int]] = set()
    for entity_id, details in rows:
        reply_id = (details or {}).get("reply_id")
        if entity_id and reply_id is not None:
            pairs.add((entity_id, int(reply_id)))
    return pairs


async def latest_versions(session, scenario_id: uuid.UUID | None) -> list[ScenarioVersion]:
    latest = (
        select(ScenarioVersion.scenario_id, func.max(ScenarioVersion.version).label("version"))
        .join(Scenario, Scenario.id == ScenarioVersion.scenario_id)
        .where(Scenario.kind == "call_intake")
        .group_by(ScenarioVersion.scenario_id)
    )
    if scenario_id is not None:
        latest = latest.where(ScenarioVersion.scenario_id == scenario_id)
    latest = latest.subquery()
    rows = await session.scalars(
        select(ScenarioVersion).join(
            latest,
            (ScenarioVersion.scenario_id == latest.c.scenario_id)
            & (ScenarioVersion.version == latest.c.version),
        )
    )
    return list(rows)


async def run(scenario_id: uuid.UUID | None, dry_run: bool) -> int:
    tts = get_tts_provider()
    if tts.method == "text":
        print("Голосового движка нет (TTS_PROVIDER / модели), нечего озвучивать.")
        return 2
    print(f"Движок: {tts.method}")
    voiced = skipped = failed = 0
    async with SessionLocal() as session:
        keep = await uploaded_replies(session)
        for version in await latest_versions(session, scenario_id):
            body = version.body
            replies = _replies(body)
            try:
                scenario = CallIntakeScenario.model_validate(body)
            except Exception as exc:  # a draft that does not validate yet
                print(f"  пропуск {version.scenario_id} v{version.version}: {exc}")
                continue
            changed = False
            for reply in replies:
                reply_id = int(reply.get("id", -1))
                audio = reply.get("audio")
                if not audio or not reply.get("text"):
                    continue
                if keeps_recording(audio, str(version.scenario_id), reply_id, keep):
                    skipped += 1
                    continue
                if dry_run:
                    voiced += 1
                    continue
                clip = await tts.synthesize(
                    reply["text"], scenario.caller.voice, scenario.caller.noise, scenario.difficulty
                )
                if clip is None:
                    failed += 1
                    continue
                stem = audio_stem(version.scenario_id, version.version, reply_id)
                for suffix in ALLOWED_AUDIO:
                    stale = (dialog.storage_root() / stem).with_suffix(suffix)
                    if stale.exists():
                        stale.unlink()
                reply["audio"] = await asyncio.to_thread(dialog._save_audio, clip, stem)
                changed = True
                voiced += 1
            if changed:
                version.body = {**body, "replies": replies}
                flag_modified(version, "body")
        if not dry_run:
            await session.commit()
    action = "к переозвучке" if dry_run else "переозвучено"
    print(
        f"Реплик {action}: {voiced}, студийных записей и записей преподавателя оставлено: "
        f"{skipped}, ошибок: {failed}"
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", type=uuid.UUID, default=None, help="только этот сценарий")
    parser.add_argument("--dry-run", action="store_true", help="только посчитать")
    args = parser.parse_args(argv)
    configure_logging()
    return asyncio.run(run(args.scenario, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
