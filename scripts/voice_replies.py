"""Studio voicing of the reference scenarios through ElevenLabs (eleven_v3).

The ``select`` dialog mode plays approved replies verbatim, so their voice can be rendered
once, ahead of time, with emotion tags the on-stand synthesizer cannot do. The result is
data: one MP3 per opening, per approved reply and per its other wording (``variants``) of a
call-intake scenario under ``data/seed/audio/<seed key>/``, which ``app.seed`` copies into
``storage/tts/seed/`` and links from the scenario body; and the card-response side (ДДС) — the
officers, the squad's reports, the specialist, the people of the organization — as banks under
``data/seed/audio/_officers/``, found by the text of a phrase. Scenarios made later on the stand
(generated or edited) keep the local synthesizer.

Runs on a machine with internet, never on the stand. ``index.json`` in every folder holds a
hash of (voice, model, tag, text) per file, so a rerun voices only what changed.

Usage (repository root; key and voice ids in ``.env.elevenlabs``, see ``--help``):
    python scripts/voice_replies.py            # voice everything missing or changed
    python scripts/voice_replies.py --dry-run  # only count characters
    python scripts/voice_replies.py --only call_2-1_zadymlenie_musoroprovoda --force
    cd backend && uv run python ../scripts/voice_replies.py --dds-only --dry-run --table t.csv
"""

from __future__ import annotations

import argparse
import csv
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
# The card-response side (ДДС): businesslike, a working background rather than panic. On the
# last report the work is done and he is tired — the sigh belongs there (user, 23.09.2026), and
# in a few more places where the person has a reason for it (user, 19.10.2026), nowhere else.
SQUAD_TAG = "[calm]"
REPORT_TAGS: dict[str, str] = {"works_done": "[tired] [exhales]"}
SIGH_TAG = "[sighs]"
EXHALE_TAG = "[exhales]"
# Tags that need a looser voice to be heard at all; the rest keep the steady default.
LOOSE_TAGS = ("[tired]", "[exhausted]", "[sighs]", "[exhales]", "[panicked]")
STABILITY_STEADY = 0.5
STABILITY_LOOSE = 0.35
# The people of the card-response side by voice (decision of 19.10.2026, by ear): the squad
# chief and the chief of the response department speak as in the cloud (Nikolay), the head of
# the organization, the squad specialist and the duty officers of other services each their own.
DDS_VOICE_ENV: dict[str, str] = {
    "chief": "ELEVENLABS_VOICE_DDS_CHIEF",
    "head": "ELEVENLABS_VOICE_DDS_HEAD",
    "specialist": "ELEVENLABS_VOICE_DDS_SPECIALIST",
    "duty": "ELEVENLABS_VOICE_DDS_DUTY",
}
# Their banks are built in code (app.dialog.officer.studio_audio, issue #59) and found by the
# text of a phrase, so one folder per speaker under ``_officers``: the squad chief per service,
# the duty officers of other services in ``_duty/<service>``, the reports and the chief's
# answers after them in ``_squad``, the specialist's complication in ``_specialist``, the people
# of the organization in ``_staff/<role>``.
OFFICERS_KEY = "_officers"
SQUAD_KEY = "_squad"
# Where the squad may be once it has reported at least once (app.dialog.officer.squad_state):
# only then can a report have gone unanswered.
REPORTED_STATES = frozenset({"response_started", "arrived", "works_started", "works_done"})
# The caller of a card answering a call back from the dispatcher (ответ заказчика 23.09.2026).
# His bank is built in code from the card, the same phrase may come up in several cards, and
# the voice differs by gender — so the folder is split by voice and the file named by the text.
CALLER_KEY = "_caller"
# Взволнованные голоса, а не спокойные дикторские (решение пользователя 24.09.2026): Vladislav
# и Lesia. Слоты те же, что и в приёме вызова, поэтому касты держим здесь, не трогая VOICE_ENV:
# перепривязка слота перезаписала бы уже оплаченные записи сценариев приёма.
CALLER_VOICE_ENV: dict[str, str] = {
    "ru_male_4": "ELEVENLABS_VOICE_MALE_2",
    "ru_female_3": "ELEVENLABS_VOICE_FEMALE_2",
}


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


def stability_of(tag: str) -> float:
    """A phrase with a breath or a sigh needs a looser voice, else the tag is not heard."""
    return STABILITY_LOOSE if any(t in tag for t in LOOSE_TAGS) else STABILITY_STEADY


def synthesize(client: httpx.Client, voice_id: str, text: str, stability: float) -> bytes:
    body = {
        "text": text,
        "model_id": MODEL_ID,
        "language_code": "ru",
        "voice_settings": {"stability": stability, "similarity_boost": 0.8},
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


def dds_phrases() -> list[tuple[str, str, str, str, str, str]]:
    """(folder under ``_officers``, file stem, voice role, tag, spoken text, who) of every fixed
    phrase the card-response side may say on a seed card, at any difficulty of a lesson, worded
    by the backend exactly as the server says it: the officers' replies and greetings by the
    squad's state, the special lines («машин нет», «Так, стоп…», the order taken), the reports
    and the answers after them, the specialist's complication, the people of the organization.
    The bank lives in the backend, so this needs its virtual environment:

        cd backend && uv run python ../scripts/voice_replies.py --dds-only

    The file name is a digest of the text as the server says it; out loud a squad number goes
    one digit at a time and the other numbers as words. What the server words on the spot from
    a stand's own card (a misheard house, a card made later) is not here and stays synthesized."""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from types import SimpleNamespace

        from app.dialog import officer
        from app.domain.evaluation.schemas import CardResponseScenario
        from app.domain.scenarios import (
            complication,
            no_vehicles,
            officers,
            rush,
            staff,
        )
        from app.providers.tts import spell_numbers
    except ImportError as exc:
        raise SystemExit(
            f"Банк ДДС берётся из бэкенда ({exc}). Запустите из backend: "
            "cd backend && uv run python ../scripts/voice_replies.py --dds-only"
        ) from exc

    classifier = json.loads((ROOT / "data/seed/classifier.json").read_text(encoding="utf-8"))
    titles = {
        row["code"]: row.get("title") or row["code"] for row in classifier.get("services", [])
    }
    staff_voices = {
        staff.ROLE_HEAD: "head",
        staff.ROLE_RESPONSE_CHIEF: "chief",
        staff.ROLE_SQUAD_SPECIALIST: "specialist",
    }
    sighs = {no_vehicles.STILL_NONE: SIGH_TAG, no_vehicles.RESOLVED: EXHALE_TAG}
    rows: dict[tuple[str, str], tuple[str, str, str, str, str, str]] = {}

    def add(key: str, text: str, role: str, who: str, tag: str = SQUAD_TAG, order: str = ""):
        text = text.strip()
        if not text or (key, text) in rows:
            return
        spoken = text.replace(order, officers.spoken_digits(order)) if order else text
        tag = sighs.get(text, tag)
        rows[(key, text)] = (
            key,
            digest_stem(text),
            role,
            tag,
            spell_numbers(spoken),
            who,
        )

    nobody = SimpleNamespace(service_calls=[])  # an attempt with no calls: the plain wording
    chief_who = "начальник аварийной бригады"
    for path in sorted(SCENARIOS_DIR.glob("card_*.json")):
        card = CardResponseScenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
        own = card.service
        order = officer.squad_order(card)
        # A lesson may be harder than the card (app.dialog.officer.lesson_scenario).
        for level in range(max(card.difficulty, 1), 4):
            s = card.model_copy(update={"difficulty": level})
            for service in dict.fromkeys([own, *(n.service for n in s.card.notified)]):
                chief = service == own
                title = titles.get(service, service)
                key = officer.officer_studio_key(s, service)
                role = "chief" if chief else "duty"
                who = chief_who if chief else f"дежурный {officers.spoken_service(service, title)}"
                ref = officer.reference_for(s, service)
                own_order = ref.order_number or officers.order_number(s, service)
                for state in officers.PROGRESS_REPLIES:
                    for repeat in (False, True):
                        built = officers.officer_scenario(
                            s, ref, title, state, repeat=repeat, chief=chief
                        )
                        for reply in built.replies:
                            if not reply.audio:
                                add(key, reply.text, role, who, order=own_order)
                        opening = built.caller.opening
                        if not chief:
                            add(key, opening, role, who)
                        elif not repeat:
                            hurried = rush.applies(level)
                            add(
                                key,
                                rush.opening(opening) if hurried else opening,
                                role,
                                who,
                            )
                        else:
                            # Called back, the chief may tell the complication the specialist
                            # could not — it comes right after the arrival, before any work —
                            # and a report the dispatcher did not take, once the squad reports.
                            lines = [opening]
                            if complication.applies(s) and state == complication.AFTER_STATUS:
                                lines.append(f"{opening.rstrip()} {complication.unheard_line(s)}")
                            for line in lines:
                                add(key, line, role, who)
                                if state not in REPORTED_STATES:
                                    continue
                                told = built.model_copy(
                                    update={
                                        "caller": built.caller.model_copy(update={"opening": line})
                                    }
                                )
                                add(
                                    key,
                                    officer._tells_missed_report(told).caller.opening,
                                    role,
                                    who,
                                )
            # The squad chief's lines built on the spot (app.dialog.officer.known_facts_reply).
            for line in (officer.ASK_ADDRESS, officer.ASK_INCIDENT, officer.TAKEN):
                add(own, line, "chief", chief_who)
            add(
                own,
                f"Принял, выезжаем. Наряд {order}.",
                "chief",
                chief_who,
                order=order,
            )
            if officers.not_ours(s):
                add(own, officers.NOT_OURS, "chief", chief_who)
            if rush.applies(level):
                add(own, rush.CUT_IN, "chief", chief_who)
            if no_vehicles.applies(level):
                for line in (
                    no_vehicles.ANNOUNCE,
                    no_vehicles.STILL_NONE,
                    no_vehicles.TO_HEAD,
                ):
                    add(own, line, "chief", chief_who)
            # The reports as the squad says them, and its answers after them.
            for report in s.reference.reports:
                said = officer.report_said(s, nobody, report)
                tag = REPORT_TAGS.get(report.status, SQUAD_TAG)
                variants = [said]
                if report.status == "works_started" and complication.applies(s):
                    variants.append(officer._after_greeting(said, complication.arrived_line(s)))
                for text in variants:
                    add(SQUAD_KEY, text, "chief", "доклад бригады", tag, order)
                    leader = officers.report_scenario(
                        s,
                        report.model_copy(update={"text": text, "audio": None}),
                        titles.get(own, own),
                    )
                    for reply in leader.replies:
                        repeats = reply.text.endswith(text)
                        add(
                            SQUAD_KEY,
                            reply.text,
                            "chief",
                            chief_who,
                            tag if repeats else SQUAD_TAG,
                            order,
                        )
            # The specialist's call about the complication, his reminder, his answers.
            if complication.applies(s):
                who = "специалист бригады (осложнение)"
                for call, tag in (
                    (complication.report(s, titles), SQUAD_TAG),
                    (complication.reminder(s), SIGH_TAG),
                ):
                    add(complication.STUDIO_KEY, call.text, "specialist", who, tag)
                    person = complication.specialist(
                        officers.report_scenario(s, call, titles.get(own, own))
                    )
                    for reply in person.replies:
                        repeats = reply.text.endswith(call.text)
                        add(
                            complication.STUDIO_KEY,
                            reply.text,
                            "specialist",
                            who,
                            tag if repeats else SQUAD_TAG,
                            order,
                        )
            # The people of the organization on the phone.
            org = officers.spoken_service(own, titles.get(own, own))
            for role_code, role in staff_voices.items():
                key = f"{officer.STUDIO_STAFF_KEY}/{role_code}"
                who = staff.ROLE_TITLES[role_code].lower()
                progresses = [
                    staff.SPECIALIST_PROGRESS.get(state, "")
                    if role_code == staff.ROLE_SQUAD_SPECIALIST
                    else staff.OFFICE_PROGRESS.get(state, officers.PROGRESS_REPLIES[state])
                    for state in officers.PROGRESS_REPLIES
                ]
                if role_code == staff.ROLE_SQUAD_SPECIALIST and complication.applies(s):
                    progresses.append(complication.specialist_line(s))
                for progress in progresses:
                    person = staff.person_scenario(role_code, s, org, progress)
                    add(key, person.caller.opening, role, who)
                    for reply in person.replies:
                        add(key, reply.text, role, who)
                add(key, staff.WRONG_CONTACT, role, who)
            head = f"{officer.STUDIO_STAFF_KEY}/{staff.ROLE_HEAD}"
            if officers.not_ours(s):
                add(head, staff.HEAD_NOT_OURS, "head", "руководитель организации")
            if no_vehicles.applies(level):
                for line in (no_vehicles.RESOLVED, no_vehicles.ASK_ADDRESS):
                    add(head, line, "head", "руководитель организации")
    return list(rows.values())


def caller_phrases() -> list[tuple[str, str, str, str]]:
    """(voice slot, file stem, tag, text) банка заявителя на обратном звонке. Банк собирается
    из карточек в бэкенде, поэтому нужен его venv, как и банку дежурного. Одинаковые фразы
    разных карточек пишутся один раз на голос: имя файла — дайджест текста, как в приёме."""
    sys.path.insert(0, str(ROOT / "backend"))
    try:
        from app.domain.evaluation.schemas import CardResponseScenario
        from app.domain.scenarios import caller_back
    except ImportError as exc:
        raise SystemExit(
            f"Банк заявителя берётся из бэкенда ({exc}). Запустите из backend: "
            "cd backend && uv run python ../scripts/voice_replies.py"
        ) from exc

    from app.providers.tts import spell_numbers

    seen: set[tuple[str, str]] = set()
    items: list[tuple[str, str, str, str]] = []
    for path in sorted(SCENARIOS_DIR.glob("card_*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        scenario = CardResponseScenario.model_validate(body)
        voice = caller_back.voice_of(scenario)
        rows = [(caller_back.DEFAULT_TAG, caller_back.OPENING)]
        # Оба состояния бригады: «пока никого нет» и «уже работают» — фразы разные.
        for arrived in (False, True):
            rows += [
                (tag, text)
                for _topic, tag, text in caller_back.callback_rows(scenario, arrived=arrived)
            ]
        for tag, text in rows:
            text = text.strip()
            if not text or (voice, text) in seen:
                continue
            seen.add((voice, text))
            # Имя файла — по тексту реплики, как его ищет бэкенд; вслух же числа идут словами,
            # иначе адрес и телефон звучат как набор цифр (правило пользователя 24.09.2026).
            items.append((voice, digest_stem(text), tag, spell_numbers(caller_back.spoken(text))))
    return items


def digest_stem(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:8]  # noqa: S324 - a file name, not security


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
    parser.add_argument(
        "--caller-only",
        action="store_true",
        help="только банк заявителя на обратном звонке, без сценариев и банка дежурного",
    )
    parser.add_argument(
        "--dds-only",
        action="store_true",
        help="только реплики ДДС (_officers без _caller): приём вызова 112 не трогается",
    )
    parser.add_argument(
        "--table",
        type=Path,
        help="CSV со всем, что пойдёт в озвучку (кто, папка, текст)",
    )
    args = parser.parse_args()

    env = load_env(ENV_FILE)
    key = env.get("ELEVENLABS_API_KEY", "")
    if not key and not args.dry_run:
        raise SystemExit(f"ELEVENLABS_API_KEY не задан ({ENV_FILE})")

    files = [] if args.caller_only or args.dds_only else sorted(SCENARIOS_DIR.glob("*.json"))
    if args.only:
        files = [f for f in files if f.stem in set(args.only)]
    todo: list[tuple[str, str, str, str, str]] = []  # key, stem, voice_id, tag, text
    who_of: dict[tuple[str, str], str] = {}  # (key, stem) → who says it, for the table
    skipped = 0
    for path in files:
        body = json.loads(path.read_text(encoding="utf-8"))
        # The squad's reports of a card-response scenario live in the DDS bank (_squad).
        if body.get("kind") != "call_intake":
            continue
        voice = (body.get("caller") or {}).get("voice") or "ru_male_1"
        items = phrases(body)
        if not items:
            continue
        voice_id = env.get(VOICE_ENV.get(voice, "ELEVENLABS_VOICE_MALE"), "")
        if not voice_id:
            raise SystemExit(f"{path.stem}: нет ElevenLabs-голоса для {voice} в {ENV_FILE}")
        folder = AUDIO_DIR / path.stem
        index_path = folder / "index.json"
        index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        for stem, tag, text in items:
            current = index.get(stem, {})
            if (
                not args.force
                and current.get("sha256") == digest(voice_id, tag, text)
                and (folder / f"{stem}.mp3").exists()
            ):
                skipped += 1
                continue
            todo.append((path.stem, stem, voice_id, tag, text))

    if not args.only:
        for folder, stem, role, tag, text, who in [] if args.caller_only else dds_phrases():
            voice_id = env.get(DDS_VOICE_ENV[role], "")
            if not voice_id:
                raise SystemExit(f"нет ElevenLabs-голоса {DDS_VOICE_ENV[role]} в {ENV_FILE}")
            key_path = f"{OFFICERS_KEY}/{folder}"
            index_path = AUDIO_DIR / key_path / "index.json"
            index = (
                json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
            )
            current = index.get(stem, {})
            if (
                not args.force
                and current.get("sha256") == digest(voice_id, tag, text)
                and (AUDIO_DIR / key_path / f"{stem}.mp3").exists()
            ):
                skipped += 1
                continue
            todo.append((key_path, stem, voice_id, tag, text))
            who_of[(key_path, stem)] = who

        # Банк заявителя на обратном звонке: та же раскладка, но папка своя на каждый голос.
        for voice, stem, tag, text in [] if args.dds_only else caller_phrases():
            voice_id = env.get(CALLER_VOICE_ENV.get(voice, "ELEVENLABS_VOICE_MALE"), "")
            if not voice_id:
                raise SystemExit(
                    f"нет ElevenLabs-голоса для {voice} в {ENV_FILE}: "
                    f"добавьте {CALLER_VOICE_ENV.get(voice)}"
                )
            key_path = f"{OFFICERS_KEY}/{CALLER_KEY}/{voice}"
            index_path = AUDIO_DIR / key_path / "index.json"
            index = (
                json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
            )
            current = index.get(stem, {})
            if (
                not args.force
                and current.get("sha256") == digest(voice_id, tag, text)
                and (AUDIO_DIR / key_path / f"{stem}.mp3").exists()
            ):
                skipped += 1
                continue
            todo.append((key_path, stem, voice_id, tag, text))

    chars = sum(len(f"{tag} {text}") for _, _, _, tag, text in todo)
    print(f"озвучить: {len(todo)} фраз, {chars} символов; без изменений: {skipped}")
    if args.table:
        with args.table.open("w", encoding="utf-8-sig", newline="") as out:
            writer = csv.writer(out, delimiter=";")
            writer.writerow(["кто", "папка", "файл", "тег", "символов", "текст"])
            for scenario_key, stem, _voice_id, tag, text in todo:
                who = who_of.get((scenario_key, stem), "")
                writer.writerow([who, scenario_key, stem, tag, len(f"{tag} {text}"), text])
        print(f"таблица: {args.table}")
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
        audio = synthesize(client, voice_id, f"{tag} {text}", stability_of(tag))
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
