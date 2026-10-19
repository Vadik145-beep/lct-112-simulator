"""Studio recordings of the card-response side (decision of 19.10.2026): every speaker has his
own folder under ``_officers`` — the duty officer of another service speaks another voice than
the squad chief — and a fixed line built on the spot, a report or the specialist's call plays the
recording of exactly its words instead of the stand's voice."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

from app.dialog import officer
from app.dialog import service as dialog
from app.domain.scenarios import complication, no_vehicles, officers, staff
from app.models import Attempt, ScenarioVersion
from tests.domain.evaluation.helpers import card_scenario

NAME = "card_gkh_1_tech_v_podezde"  # own service gkh; territorial_oiv among the notified
ROOT = Path(__file__).resolve().parents[3]


def _record(storage: Path, folder: str, text: str) -> str:
    relative = f"{officer.STUDIO_OFFICERS_DIR}/{folder}/{officer._text_key(text)}.mp3"
    (storage / relative).parent.mkdir(parents=True, exist_ok=True)
    (storage / relative).write_bytes(b"studio")
    return relative


def _call(kind: str, **extra: object) -> dict:
    return {"id": uuid.uuid4().hex, "service": "gkh", "kind": kind, "dialog": [], **extra}


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(dialog, "storage_root", lambda: tmp_path)

    def no_provider():
        raise AssertionError("the recording must be played, not synthesized")

    monkeypatch.setattr(dialog, "get_tts_provider", no_provider)
    return tmp_path


def test_duty_officer_of_another_service_has_his_own_folder(storage: Path) -> None:
    scenario = card_scenario(NAME)
    greeting = officers.greeting("territorial_oiv", "Территориальные ОИВ")
    chief_take = _record(storage, "territorial_oiv", greeting)  # the chief's voice: not his
    duty_take = _record(storage, "_duty/territorial_oiv", greeting)

    assert officer.officer_studio_key(scenario, "gkh") == "gkh"
    assert officer.officer_studio_key(scenario, "territorial_oiv") == "_duty/territorial_oiv"
    built = officer.officer_scenario(
        scenario, "territorial_oiv", "Территориальные ОИВ", Attempt(service_calls=[])
    )
    played = {r.audio for r in built.replies if r.text == greeting}
    assert played == {duty_take}
    assert chief_take not in {r.audio for r in built.replies}


def test_every_speaker_is_looked_up_in_his_folder() -> None:
    scenario = card_scenario(NAME)
    other = officers.report_scenario(scenario, scenario.reference.reports[0], "ЖКХ")
    assert officer.studio_key(scenario, _call(officer.KIND_OUTGOING), other) == "gkh"
    outside = _call(officer.KIND_OUTGOING, service="territorial_oiv")
    assert officer.studio_key(scenario, outside, other) == "_duty/territorial_oiv"
    head = _call(officer.KIND_STAFF, **{no_vehicles.ROLE_KEY: staff.ROLE_HEAD})
    assert officer.studio_key(scenario, head, other) == "_staff/head"
    report = _call(officer.KIND_REPORT, report_status="arrived")
    assert officer.studio_key(scenario, report, other) == "_squad"
    for status in (complication.STATUS, complication.REMINDER_STATUS):
        call = _call(officer.KIND_REPORT, report_status=status)
        assert officer.studio_key(scenario, call, other) == complication.STUDIO_KEY


async def test_report_plays_the_bank_recording_of_its_words(storage: Path) -> None:
    """The report said in today's wording has no recording in the scenario body (its old
    words were recorded): the bank's recording of exactly the said words plays."""
    scenario = card_scenario(NAME)
    report = scenario.reference.reports[-1]
    said = officer.report_said(scenario, Attempt(service_calls=[]), report)
    assert said != report.text  # the seed card still holds the old wording
    take = _record(storage, "_squad", said)
    leader = officers.report_scenario(
        scenario, report.model_copy(update={"text": said, "audio": None}), "ЖКХ"
    )
    version = ScenarioVersion(version=1, body={})
    current = report.model_copy(update={"text": said, "audio": None})
    assert await officer._report_audio(version, leader, current) == take
    # Every «{report}» answer of the leader is the same recording.
    assert take in {r.audio for r in officer.with_studio_audio(leader, "_squad").replies}


async def test_specialist_call_plays_his_recording(storage: Path) -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = complication.report(scenario, {})
    take = _record(storage, complication.STUDIO_KEY, call.text)
    person = complication.specialist(officers.report_scenario(scenario, call, "ЖКХ"))
    version = ScenarioVersion(version=1, body={})
    studio = complication.STUDIO_KEY
    assert await officer._report_audio(version, person, call, studio=studio) == take


async def test_greeting_of_another_service_plays_the_duty_officer(storage: Path) -> None:
    scenario = card_scenario(NAME)
    built = officer.officer_scenario(
        scenario, "territorial_oiv", "Территориальные ОИВ", Attempt(service_calls=[])
    )
    take = _record(storage, "_duty/territorial_oiv", built.caller.opening)
    version = ScenarioVersion(version=1, body={})
    studio = officer.officer_studio_key(scenario, "territorial_oiv")
    audio = await officer._greeting_audio(version, built, "territorial_oiv", studio=studio)
    assert audio == take


def test_special_lines_are_found_by_their_words(storage: Path) -> None:
    """«Машин нет», «Так, стоп…» and the like are built on the spot without a recording;
    ``say`` looks them up by their words in the speaker's folder."""
    take = _record(storage, "gkh", no_vehicles.ANNOUNCE)
    assert officer.studio_audio("gkh", no_vehicles.ANNOUNCE) == take
    assert officer.studio_audio("_duty/gkh", no_vehicles.ANNOUNCE) is None


def _voicing_script():
    path = ROOT / "scripts" / "voice_replies.py"
    spec = importlib.util.spec_from_file_location("voice_replies", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_voicing_script_collects_what_the_server_says() -> None:
    """The DDS bank of ``scripts/voice_replies.py`` holds the phrases worded by the server, in
    the folders the server looks in, with no call-intake folder among them."""
    script = _voicing_script()
    rows = script.dds_phrases()
    by_place = {(folder, stem): (role, tag, spoken) for folder, stem, role, tag, spoken, _ in rows}
    scenario = card_scenario(NAME)
    hard = scenario.model_copy(update={"difficulty": 3})

    def found(folder: str, text: str) -> tuple[str, str, str]:
        return by_place[(folder, officer._text_key(text))]

    assert found("gkh", no_vehicles.ANNOUNCE)[0] == "chief"
    assert found("gkh", no_vehicles.STILL_NONE)[1] == "[sighs]"
    assert found("_staff/head", no_vehicles.RESOLVED)[:2] == ("head", "[exhales]")
    assert found("_staff/response_chief", staff.WRONG_CONTACT)[0] == "chief"
    assert found(complication.STUDIO_KEY, complication.report(hard, {}).text)[0] == "specialist"
    done = scenario.reference.reports[-1]
    said = officer.report_said(scenario, Attempt(service_calls=[]), done)
    assert found("_squad", said)[1] == "[tired] [exhales]"
    greeting = officers.greeting("territorial_oiv", "Территориальные ОИВ")
    assert found("_duty/territorial_oiv", greeting)[0] == "duty"
    # The squad number out loud: one digit at a time.
    order = officer.squad_order(scenario)
    for folder, text in (
        ("gkh", f"Принял, выезжаем. Наряд {order}."),
        ("_squad", f"Наряд {order}, по нему и работаем."),
        (complication.STUDIO_KEY, f"Наряд {order}, по нему и работаем."),
    ):
        spoken = found(folder, text)[2]
        assert officers.spoken_digits(order) in spoken, (folder, spoken)
    # No squad number anywhere is read as a number («пять тысяч двести…»).
    assert not any("тысяч" in spoken for *_, spoken, _who in rows)
    assert not any(folder.startswith(("call_", "_fallback", "_caller")) for folder, *_ in rows)
