"""Студийные записи заявителя закреплены за кнопкой: у каждой реплики обратного звонка есть
свой файл, он лежит там, где его ищет диалог, и звучит своим голосом.

Записи сделаны ``scripts/voice_replies.py --caller-only`` (eleven_v3), лежат в
``data/seed/audio/_officers/_caller/<голос>/`` и попадают на стенд сидером.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.domain.evaluation.schemas import CardResponseScenario
from app.domain.scenarios import caller_back
from app.seed import copy_bank_audio

DATA_DIR = Path(__file__).resolve().parents[2].parent / "data"
SCENARIOS_DIR = DATA_DIR / "seed" / "scenarios"
BANK_DIR = DATA_DIR / "seed" / "audio" / "_officers" / "_caller"

pytestmark = pytest.mark.skipif(not BANK_DIR.is_dir(), reason="нет записей заявителя")


def card_scenarios() -> list[CardResponseScenario]:
    return [
        CardResponseScenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(SCENARIOS_DIR.glob("card_*.json"))
    ]


@pytest.fixture(scope="module")
def storage(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Хранилище стенда, как его собирает сидер из поставки."""
    root = tmp_path_factory.mktemp("storage")
    copy_bank_audio(DATA_DIR, root, "_officers")
    return root


@pytest.fixture(autouse=True)
def _storage_root(storage: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dialog import service as dialog

    monkeypatch.setattr(dialog, "storage_root", lambda: storage)


def test_every_phrase_of_every_card_has_a_recording() -> None:
    from app.dialog import officer

    missing: list[str] = []
    for scenario in card_scenarios():
        built = caller_back.callback_scenario(scenario)
        key = f"{officer.STUDIO_CALLER_KEY}/{built.caller.voice}"
        texts = [built.caller.opening, *(reply.text for reply in built.replies)]
        missing += [text for text in texts if officer.studio_audio(key, text) is None]
    assert not missing, f"без записи осталось {len(missing)}: {missing[:3]}"


def test_the_arrived_answer_is_recorded_too() -> None:
    """Реплика «приехали, уже работают» звучит только после прибытия бригады — её легко
    забыть при озвучке, поэтому проверяем отдельно."""
    from app.dialog import officer

    for scenario in card_scenarios():
        built = caller_back.callback_scenario(scenario, arrived=True)
        key = f"{officer.STUDIO_CALLER_KEY}/{built.caller.voice}"
        arrived = [r.text for r in built.replies if "работают" in r.text]
        assert arrived, "реплики о прибывшей бригаде нет в банке"
        assert all(officer.studio_audio(key, text) for text in arrived)


def test_recordings_are_split_by_voice() -> None:
    """Одна и та же фраза мужским и женским голосом — два разных файла, иначе заявитель
    заговорит не своим голосом."""
    from app.dialog import officer

    male = officer.studio_audio(
        f"{officer.STUDIO_CALLER_KEY}/{caller_back.VOICE_MALE}", caller_back.OPENING
    )
    female = officer.studio_audio(
        f"{officer.STUDIO_CALLER_KEY}/{caller_back.VOICE_FEMALE}", caller_back.OPENING
    )
    assert male and female and male != female


def test_recordings_were_made_by_the_model_we_agreed_on() -> None:
    """eleven_v3 с тегами эмоций: на flash теги читаются как текст."""
    for index_path in BANK_DIR.glob("*/index.json"):
        rows = json.loads(index_path.read_text(encoding="utf-8"))
        assert rows, f"пустой индекс {index_path}"
        for stem, row in rows.items():
            assert row["model"] == "eleven_v3", f"{index_path.parent.name}/{stem}"
            assert row["tag"].startswith("["), f"{index_path.parent.name}/{stem}: нет тега"
            assert "[panicked]" not in row["tag"], "паники в обратном звонке быть не должно"


async def test_the_dialog_hands_the_recording_to_the_call() -> None:
    """Реплика, выбранная движком, приходит с готовой записью, а не с синтезом на лету."""
    from app.dialog import officer
    from app.providers.dialog import ROLE_CALLER, ButtonsDialog, DialogContext

    scenario = card_scenarios()[0]
    built = caller_back.callback_scenario(scenario)
    built = officer.with_studio_audio(built, f"{officer.STUDIO_CALLER_KEY}/{built.caller.voice}")
    ctx = DialogContext(scenario=built, history=[], conversation_id="t", role=ROLE_CALLER)
    reply = await ButtonsDialog().reply(ctx, "Назовите адрес, куда вызывали.")
    assert reply.audio, "движок отдал реплику без записи"
    assert reply.audio.startswith("tts/seed/_officers/_caller/")
