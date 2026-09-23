"""Studio recordings of the seed scenarios: ``data/seed/audio/<key>/`` is copied into storage
and linked from the body, the opening plays the recording without any voice provider, and the
squad's reports play theirs (issue #67)."""

import json
from pathlib import Path

import pytest

from app import seed as seed_module
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CallIntakeScenario, CardResponseScenario
from app.models import Attempt, ScenarioVersion
from app.providers.dialog import CallerReply

BODY = {
    "kind": "call_intake",
    "title": "Задымление",
    "difficulty": 2,
    "caller": {"persona": "worried_resident", "voice": "ru_male_1", "opening": "Алло, 112?"},
    "replies": [
        {
            "id": 1,
            "topic": "address",
            "text": "Улица Ленина, дом пять.",
            "approved": True,
            "variants": [{"text": "Ленина, пять."}, {"text": "Дом пять по улице Ленина."}],
        },
        {"id": 2, "topic": "injured", "text": "Нет, никто.", "approved": False},
        {"id": 3, "topic": "repeat", "text": "Что? Повторите!", "approved": True},
    ],
    "required_topics": ["address"],
    "reference_card": {"incident_type": "1.1", "address": {"street": "Ленина", "house": "5"}},
}


def _layout(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    folder = data_dir / seed_module.AUDIO_DIR / "call_test"
    folder.mkdir(parents=True)
    for name, content in files.items():
        (folder / name).write_bytes(content)
    return data_dir, tmp_path / "storage"


def test_attach_audio_links_opening_and_approved_replies(tmp_path: Path) -> None:
    data_dir, storage = _layout(
        tmp_path,
        {"opening.mp3": b"o", "r1.mp3": b"1", "r1-v2.mp3": b"12", "r2.mp3": b"2", "r3.wav": b"3"},
    )
    body = json.loads(json.dumps(BODY))
    linked = seed_module._attach_audio(body, "call_test", data_dir, storage)

    assert linked == 4
    assert body["caller"]["opening_audio"] == "tts/seed/call_test/opening.mp3"
    assert [r.get("audio") for r in body["replies"]] == [
        "tts/seed/call_test/r1.mp3",
        None,  # not approved: the recording is ignored even though the file exists
        "tts/seed/call_test/r3.wav",
    ]
    # Other wordings: the first has no recording yet (Piper), the second is linked.
    assert [v.get("audio") for v in body["replies"][0]["variants"]] == [
        None,
        "tts/seed/call_test/r1-v2.mp3",
    ]
    copied = storage / seed_module.AUDIO_STORAGE / "call_test"
    assert sorted(p.name for p in copied.iterdir()) == [
        "opening.mp3",
        "r1-v2.mp3",
        "r1.mp3",
        "r3.wav",
    ]
    assert (copied / "r1.mp3").read_bytes() == b"1"
    # The body validates with the new field, so the dialog engine sees the recording.
    assert CallIntakeScenario.model_validate(body).caller.opening_audio.endswith("opening.mp3")


def test_attach_audio_is_deterministic_and_refreshes_changed_files(tmp_path: Path) -> None:
    data_dir, storage = _layout(tmp_path, {"r1.mp3": b"first"})
    first = json.loads(json.dumps(BODY))
    seed_module._attach_audio(first, "call_test", data_dir, storage)
    second = json.loads(json.dumps(BODY))
    seed_module._attach_audio(second, "call_test", data_dir, storage)
    assert first == second, "a rerun must not change the body, or the seed makes new versions"

    (data_dir / seed_module.AUDIO_DIR / "call_test" / "r1.mp3").write_bytes(b"second take")
    seed_module._attach_audio(json.loads(json.dumps(BODY)), "call_test", data_dir, storage)
    assert (storage / seed_module.AUDIO_STORAGE / "call_test" / "r1.mp3").read_bytes() == (
        b"second take"
    )


CARD_BODY = {
    "kind": "card_response",
    "title": "Нет отопления",
    "service": "moek",
    "card": {"incident_type": "1.1", "signs": [], "flags": {}, "description": "Нет тепла."},
    "reference": {
        "decision": "accept",
        "status_chain": [{"status": "accepted"}, {"status": "arrived"}],
        "reports": [
            {"status": "response_started", "text": "Выехали.", "after_seconds": 30},
            {"status": "arrived", "text": "Прибыли на адрес.", "after_seconds": 45},
        ],
    },
}


def test_attach_audio_links_the_squad_reports(tmp_path: Path) -> None:
    data_dir, storage = _layout(tmp_path, {"report-arrived.mp3": b"arrived"})
    body = json.loads(json.dumps(CARD_BODY))
    linked = seed_module._attach_audio(body, "call_test", data_dir, storage)
    assert linked == 1
    reports = body["reference"]["reports"]
    assert reports[1]["audio"] == "tts/seed/call_test/report-arrived.mp3"
    assert "audio" not in reports[0]  # no recording yet: the stand voices it itself
    copied = storage / seed_module.AUDIO_STORAGE / "call_test" / "report-arrived.mp3"
    assert copied.read_bytes() == b"arrived"
    scenario = CardResponseScenario.model_validate(body)
    assert scenario.reference.reports[1].audio.endswith("report-arrived.mp3")
    assert scenario.reference.reports[0].audio is None


def test_officer_bank_is_copied_and_found_by_the_dialog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #59: the officers' studio bank lives per service, the dialog finds a phrase by
    its text, and a phrase without a recording is left to the stand."""
    from app.dialog import officer

    data_dir = tmp_path / "data"
    folder = data_dir / seed_module.AUDIO_DIR / seed_module.OFFICERS_AUDIO_KEY / "gkh"
    folder.mkdir(parents=True)
    greeting = "Дежурный ЕДЦ ЖКХ, слушаю."
    (folder / f"{officer._text_key(greeting)}.mp3").write_bytes(b"hello")
    storage = tmp_path / "storage"

    assert seed_module.copy_officer_audio(data_dir, storage) == 1
    copied = (
        storage
        / seed_module.AUDIO_STORAGE
        / seed_module.OFFICERS_AUDIO_KEY
        / "gkh"
        / f"{officer._text_key(greeting)}.mp3"
    )
    assert copied.read_bytes() == b"hello"

    monkeypatch.setattr(dialog, "storage_root", lambda: storage)
    assert officer.studio_audio("gkh", greeting) == (
        f"tts/seed/_officers/gkh/{officer._text_key(greeting)}.mp3"
    )
    assert officer.studio_audio("gkh", "Этой фразы никто не записывал.") is None
    assert officer.studio_audio("moek", greeting) is None  # запись своя у каждой службы


def test_officer_names_his_service_the_way_people_say_it() -> None:
    """The reference titles carry brackets and paperwork; the officer says a spoken name."""
    from app.domain.scenarios import officers

    assert officers.greeting("gkh", "Городское хозяйство (ЕДЦ ЖКХ)") == "Дежурный ЕДЦ ЖКХ, слушаю."
    assert officers.greeting("103", "Служба 103 (скорая помощь)") == (
        "Дежурный скорой помощи, слушаю."
    )
    # A service with no spoken name of its own keeps its title without the brackets.
    assert officers.spoken_service("unknown_service", "Какая-то служба (с уточнением)") == (
        "Какая-то служба"
    )


def test_attach_audio_skips_other_kinds_and_missing_folders(tmp_path: Path) -> None:
    data_dir, storage = _layout(tmp_path, {"opening.mp3": b"o"})
    card = {"kind": "card_response", "caller": {"opening": "x"}}
    assert seed_module._attach_audio(card, "call_test", data_dir, storage) == 0
    assert "opening_audio" not in card["caller"]
    body = json.loads(json.dumps(BODY))
    assert seed_module._attach_audio(body, "call_other", data_dir, storage) == 0
    assert body == BODY
    assert not storage.exists()


async def test_opening_audio_plays_the_recording_without_tts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = json.loads(json.dumps(BODY))
    body["caller"]["opening_audio"] = "tts/seed/call_test/opening.mp3"
    scenario = CallIntakeScenario.model_validate(body)
    version = ScenarioVersion(version=1, body=body)

    def no_provider():
        raise AssertionError("the recording must be used, not synthesized")

    monkeypatch.setattr(dialog, "get_tts_provider", no_provider)
    assert await dialog.opening_audio(version, scenario) == "tts/seed/call_test/opening.mp3"


def _reply(reply_id: int) -> CallerReply:
    return CallerReply(
        text="Улица Ленина, дом пять.",
        topics=["address"],
        operator_topics=["address"],
        reply_id=reply_id,
        audio="tts/seed/call_test/r1.mp3",
        method="select",
    )


def test_pick_variant_rotates_wordings_and_keeps_facts() -> None:
    body = json.loads(json.dumps(BODY))
    body["replies"][0]["variants"][1]["audio"] = "tts/seed/call_test/r1-v2.mp3"
    scenario = CallIntakeScenario.model_validate(body)
    attempt = Attempt(dialog=[])
    seen: dict[int, tuple[str, str | None]] = {}
    for _ in range(3):
        picked = dialog.pick_variant(attempt, scenario, _reply(1))
        assert picked.variant not in seen, "a call plays every wording before repeating one"
        seen[picked.variant] = (picked.text, picked.audio)
        attempt.dialog.append({"role": "caller", "reply_id": 1, "variant": picked.variant})
    assert seen == {
        0: ("Улица Ленина, дом пять.", "tts/seed/call_test/r1.mp3"),
        1: ("Ленина, пять.", None),
        2: ("Дом пять по улице Ленина.", "tts/seed/call_test/r1-v2.mp3"),
    }
    # All played: any of them again, still one of the scenario's wordings.
    again = dialog.pick_variant(attempt, scenario, _reply(1))
    assert again.variant in seen
    assert again.topics == ["address"]


def test_pick_variant_leaves_replies_without_wordings_alone() -> None:
    scenario = CallIntakeScenario.model_validate(BODY)
    attempt = Attempt(dialog=[])
    plain = _reply(3)
    assert dialog.pick_variant(attempt, scenario, plain) is plain
    generated = CallerReply(text="Своими словами", topics=[], operator_topics=[], method="generate")
    assert dialog.pick_variant(attempt, scenario, generated) is generated
