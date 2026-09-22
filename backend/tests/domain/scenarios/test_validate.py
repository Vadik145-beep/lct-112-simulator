"""Reference-code checks, approved-part protection and the exported body schemas."""

from __future__ import annotations

import json

from app.domain.scenarios import generated
from app.domain.scenarios.validate import (
    ReferenceCodes,
    approve_all,
    changed_approved_parts,
    check_body,
    fill_from_reference,
    is_fully_approved,
)

ROW = {
    "code": "1.1.1.1",
    "group_code": "1",
    "sign1": "на улице",
    "sign2": "мусор",
    "sign3": "открытое пламя",
    "final_title": "пожар: мусор",
    "service_rules": [
        {"service": "101", "when": [], "notify": True},
        {"service": "102", "when": ["injured"], "notify": True},
    ],
}
REFS = ReferenceCodes(
    incident_types={"1.1.1.1": ROW},
    flags={"injured", "no_access"},
    services={"101", "102", "gkh"},
    tickets={"1-1"},
)


def call_body(**overrides) -> dict:
    body = {
        "kind": "call_intake",
        "title": "Пожар мусора",
        "ticket_ref": "1-1",
        "difficulty": 1,
        "caller": {"persona": "calm", "voice": "ru_male_1", "noise": "street", "opening": "Алло"},
        "replies": [
            {
                "id": 1,
                "topic": "address",
                "text": "Берзарина, 21",
                "approved": True,
                "audio": "tts/x/v1/r1.mp3",
            },
            {"id": 2, "topic": "injured", "text": "Нет", "approved": False, "audio": None},
        ],
        "required_topics": ["address", "injured"],
        "reference_card": {
            "signs_path": ["что-то не то"],
            "incident_type": "1.1.1.1",
            "flags": {"injured": True},
            "address": {"street": "ул. Берзарина", "house": "21"},
            "caller": {"name": None},
            "description": "Горит мусор.",
            "expected_services": ["gkh"],
        },
    }
    body.update(overrides)
    return body


def test_fill_from_reference_rewrites_signs_and_services() -> None:
    filled = fill_from_reference(call_body(), REFS)
    card = filled["reference_card"]
    assert card["signs_path"] == ["на улице", "мусор", "открытое пламя"]
    assert card["expected_services"] == ["101", "102"]  # injured flag adds the police


def test_check_body_reports_unknown_codes() -> None:
    assert check_body(fill_from_reference(call_body(), REFS), REFS) == []
    bad = call_body()
    bad["reference_card"]["incident_type"] = "9.9.9.9"
    bad["reference_card"]["flags"] = {"weird": True}
    bad["replies"][0]["topic"] = "nonsense"
    bad["caller"]["persona"] = "alien"
    problems = check_body(bad, REFS)
    assert any("9.9.9.9" in p for p in problems)
    assert any("weird" in p for p in problems)
    assert any("nonsense" in p for p in problems)
    assert any("alien" in p for p in problems)


def test_check_body_rejects_broken_schema() -> None:
    problems = check_body({"kind": "call_intake", "title": "x"}, REFS)
    assert len(problems) == 1 and problems[0].startswith("Тело сценария")


def test_approved_reply_and_reference_are_locked() -> None:
    old = call_body()
    new = call_body()
    new["replies"][0]["text"] = "Другой текст"
    assert changed_approved_parts(old, new) == ["реплика 1 утверждена, поле «text» менять нельзя"]

    new = call_body()
    new["replies"] = new["replies"][1:]  # approved reply removed
    assert changed_approved_parts(old, new) == ["реплика 1 утверждена и не может быть удалена"]

    new = call_body()
    new["replies"][1]["text"] = "Правка неутверждённой"  # allowed
    new["reference_card"]["description"] = "Изменённый эталон"  # reference not approved yet
    assert changed_approved_parts(old, new) == []

    approved = approve_all(old)
    assert is_fully_approved(approved)
    new = dict(approved)
    new["reference_card"] = {**approved["reference_card"], "description": "Иначе"}
    assert changed_approved_parts(approved, new) == [
        "эталон утверждён; изменить его можно только новой версией («Переделать»)"
    ]


def test_card_response_checks() -> None:
    body = {
        "kind": "card_response",
        "title": "Пожар мусора",
        "service": "gkh",
        "card": {
            "incident_type": "1.1.1.1",
            "signs": [],
            "flags": {"injured": False},
            "description": "Горит мусор.",
            "notified": [{"service": "101", "status": "Получена службой"}],
        },
        "reference": {
            "decision": "reject",
            "reject_reason": "made_up",
            "status_chain": [{"status": "flying"}],
        },
    }
    problems = check_body(body, REFS)
    assert any("made_up" in p for p in problems)
    assert any("flying" in p for p in problems)
    assert fill_from_reference(body, REFS)["card"]["signs"] == [
        "на улице",
        "мусор",
        "открытое пламя",
    ]


def test_brigade_reports_the_teacher_edits_are_checked() -> None:
    """The squad's timeline (issue #103): only progress statuses, one report per status,
    a text to say and a delay the sweep can work with."""
    body = {
        "kind": "card_response",
        "title": "Нет отопления",
        "service": "gkh",
        "card": {
            "incident_type": "1.1.1.1",
            "signs": [],
            "flags": {"injured": False},
            "description": "Нет отопления в доме.",
            "notified": [{"service": "gkh", "status": "Получена службой"}],
        },
        "reference": {
            "decision": "accept",
            "status_chain": [{"status": "accepted"}, {"status": "arrived"}],
            "reports": [
                {"status": "arrived", "text": "Прибыли на адрес.", "after_seconds": 45},
            ],
        },
    }
    assert check_body(body, REFS) == []

    broken = json.loads(json.dumps(body))
    broken["reference"]["reports"] = [
        {"status": "accepted", "text": "Приняли.", "after_seconds": 45},
        {"status": "arrived", "text": "Прибыли.", "after_seconds": 1},
        {"status": "arrived", "text": "   ", "after_seconds": 45},
    ]
    problems = check_body(broken, REFS)
    assert any("accepted" in p for p in problems)
    assert any("Два доклада" in p for p in problems)
    assert any("от 5 до 3600" in p for p in problems)
    assert any("без текста" in p for p in problems)

    # No reports at all is a valid choice: the trainee sets the progress statuses alone.
    off = json.loads(json.dumps(body))
    off["reference"]["reports"] = []
    assert check_body(off, REFS) == []


def test_generation_schema_constrains_codes() -> None:
    schema = generated.call_intake_schema(["1.1.1.1", "2.1.0.0", "1.1.1.1"])
    assert schema["properties"]["incident_type"]["enum"] == ["1.1.1.1", "2.1.0.0"]
    reply_schema = schema["$defs"]["GeneratedReply"]["properties"]["topic"]
    assert set(reply_schema["enum"]) == set(generated.TOPIC_CODES)
    assert schema["properties"]["persona"]["enum"] == list(generated.PERSONA_CODES)
    card = generated.card_response_schema(["1.1.1.1"])
    assert set(card["properties"]["reject_reason"]["anyOf"][0]["enum"]) == set(
        generated.REJECT_CODES
    )


def test_exported_body_schemas_match_models() -> None:
    for kind, path in generated.BODY_SCHEMA_FILES.items():
        assert path.exists(), f"нет {path.name}: запустите export_body_schemas()"
        assert json.loads(path.read_text(encoding="utf-8")) == generated.body_schema(kind)
