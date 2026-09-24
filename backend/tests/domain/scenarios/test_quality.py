"""Remarks on a scenario: speech that cannot be read aloud, a card that does not match it."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.domain.scenarios import quality

SCENARIOS_DIR = Path(__file__).resolve().parents[4] / "data" / "seed" / "scenarios"

AUDIO_DIR = SCENARIOS_DIR.parent / "audio"


def _studio(key: str) -> bool:
    """The ten reference scenarios: every reply of theirs is recorded in several wordings
    (``r1-v1``), and they were written before these checks existed."""
    index = AUDIO_DIR / key / "index.json"
    if not index.is_file():
        return False
    return any(re.fullmatch(r"r\d+-v\d+", stem) for stem in json.loads(index.read_text(encoding="utf-8")))


BODY = {
    "kind": "call_intake",
    "title": "Задымление",
    "caller": {"opening": "Горит мусорка во дворе!"},
    "replies": [
        {"id": 1, "topic": "address", "text": "Улица Ленина, дом пять."},
        {"id": 2, "topic": "repeat", "text": "Повторите, пожалуйста."},
        {"id": 3, "topic": "unknown", "text": "Не знаю."},
    ],
    "required_topics": ["address"],
    "reference_card": {
        "description": "Горит мусорный контейнер во дворе.",
        "description_keywords": ["мусорный контейнер"],
        "address": {"street": "улица Ленина", "house": "5"},
        "caller": {"phone": "916-896-32-54"},
    },
}


def test_clean_scenario_has_no_remarks() -> None:
    assert quality.review(BODY) == []


def test_brackets_shorthand_and_digits_in_speech_are_named() -> None:
    body = json.loads(json.dumps(BODY))
    body["replies"][0]["text"] = "Ленина, д. 5, кв. 12 (при уточнении — корпус 2)."
    remarks = " ".join(quality.review(body))
    assert "скобки" in remarks
    assert "кв." in remarks and "д. с номером" in remarks
    assert "цифры" in remarks


def test_card_that_does_not_match_its_own_description() -> None:
    body = json.loads(json.dumps(BODY))
    body["reference_card"]["description_keywords"] = ["взрыв газа"]
    body["reference_card"]["address"] = {}
    body["reference_card"]["caller"] = {}
    remarks = quality.review(body)
    assert any("взрыв газа" in r for r in remarks)
    assert any("нет адреса" in r for r in remarks)
    assert any("нет телефона" in r for r in remarks)


def test_required_topic_without_a_reply_is_named() -> None:
    body = json.loads(json.dumps(BODY))
    body["required_topics"] = ["address", "injured"]
    assert any("injured" in r for r in quality.review(body))


def test_delivered_scenarios_pass_their_own_checks() -> None:
    """The 96 of the delivery were checked by hand against exactly these rules."""
    written_by_hand = [
        p for p in sorted(SCENARIOS_DIR.glob("call_*.json")) if not _studio(p.stem)
    ]
    assert len(written_by_hand) > 50
    for path in written_by_hand:
        body = json.loads(path.read_text(encoding="utf-8"))
        assert quality.review(body) == [], f"{path.stem}: {quality.review(body)}"
