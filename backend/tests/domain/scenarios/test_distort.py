"""Planted operator mistakes in generated cards (issues #35, #265): exactly 0/1/2 by
difficulty, kinds, the description naming the truth «со слов заявителя», determinism,
preferred kinds, and the generation providers applying them."""

from __future__ import annotations

import json
import random

import pytest

from app.domain.evaluation.schemas import parse_scenario
from app.domain.scenarios import distort
from app.domain.scenarios.template import ServiceInfo
from app.domain.scenarios.validate import ReferenceCodes, check_body
from app.providers.generation import (
    GenerationContext,
    GenerationRequest,
    TemplateGeneration,
)
from tests.domain.evaluation.helpers import SCENARIOS_DIR

CATALOGUE = {
    "101": ServiceInfo("101", False, "Служба 101 (МЧС)"),
    "102": ServiceInfo("102", False, "Служба 102 (полиция)"),
    "gkh": ServiceInfo("gkh", True, "Диспетчерская ЖКХ"),
    "moek": ServiceInfo("moek", True, "МОЭК"),
    "moslift": ServiceInfo("moslift", True, "Мослифт"),
    "territorial_oiv": ServiceInfo("territorial_oiv", True, "Территориальные ОИВ"),
}
STREETS = ["улица Свободы", "улица Славы", "улица Свободная", "Ленинский проспект"]


def seed_card(name: str | None = None) -> dict:
    path = SCENARIOS_DIR / f"{name or 'card_gkh_1_tech_v_podezde'}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def context(type_rows: list[dict]) -> distort.Context:
    return distort.Context(type_rows, CATALOGUE, STREETS)


def test_difficulty_one_never_gets_a_mistake(type_rows: list[dict]) -> None:
    body = seed_card()
    assert body["difficulty"] == 1
    for title in ("а", "б", "в", "г", "д", "е", "ж", "з"):
        out = distort.inject_by_difficulty(
            {**body, "title": title}, type_rows=type_rows, catalogue=CATALOGUE
        )
        assert "injected_errors" not in out


@pytest.mark.parametrize(("difficulty", "count"), [(2, 1), (3, 2)])
def test_difficulty_sets_the_exact_number_of_mistakes(
    type_rows: list[dict], difficulty: int, count: int
) -> None:
    body = {**seed_card(), "difficulty": difficulty}
    for i in range(60):
        out = distort.inject_by_difficulty(
            {**body, "title": f"Карточка {i}"},
            type_rows=type_rows,
            catalogue=CATALOGUE,
            streets=STREETS,
        )
        errors = out.get("injected_errors") or []
        assert len(errors) == count, i
        assert len({e["field"] for e in errors}) == count
        # One address mistake per card.
        assert sum(e["field"].startswith("address.") for e in errors) <= 1


def test_preferred_kind_is_planted_first(type_rows: list[dict]) -> None:
    body = {**seed_card(), "difficulty": 2}
    for i in range(10):
        out = distort.inject_by_difficulty(
            {**body, "title": f"Карточка {i}"},
            type_rows=type_rows,
            catalogue=CATALOGUE,
            prefer=["injured"],
        )
        assert [e["field"] for e in out["injected_errors"]] == ["flags.injured"]


def test_planting_is_deterministic(type_rows: list[dict]) -> None:
    body = {**seed_card(), "difficulty": 3}
    first = distort.inject_by_difficulty(body, type_rows=type_rows, catalogue=CATALOGUE)
    second = distort.inject_by_difficulty(body, type_rows=type_rows, catalogue=CATALOGUE)
    assert first == second
    assert body.get("injected_errors") is None  # the input is not modified


def test_every_kind_keeps_the_truth_readable_and_validates(type_rows: list[dict]) -> None:
    ctx = context(type_rows)
    for kind in distort.DISTORTIONS:
        body = seed_card()
        card = distort.inject(body, count=1, rng=random.Random(kind), ctx=ctx, prefer=[kind])  # noqa: S311
        assert len(card["injected_errors"]) == 1, kind
        error = card["injected_errors"][0]
        assert distort.TRUTH_KEY not in error
        scenario = parse_scenario(card)
        assert scenario.injected_errors[0].field == error["field"]
        assert error["wrong_value"] != error["correct_value"]
        description = card["card"]["description"]
        assert description.startswith(body["card"]["description"].rstrip(" ."))
        assert "Со слов заявителя:" not in description
        told = description[len(body["card"]["description"]) :]
        if kind == "house":
            assert card["card"]["address"]["house"] != body["card"]["address"]["house"]
            assert "точный адрес — улица Свободы, дом 42." in told
        elif kind == "street":
            assert card["card"]["address"]["street"] in {"улица Славы", "улица Свободная"}
            assert "точный адрес — улица Свободы, дом 42." in told
        elif kind == "incident_type":
            assert card["card"]["incident_type"] != body["card"]["incident_type"]
            assert error["correct_label"].split(":")[0].lower() in told
        elif kind == "injured":
            assert card["card"]["flags"]["injured"] is True
            # The description already says so: nothing is added.
            assert description == body["card"]["description"]
        elif kind == "wrong_service":
            assert card["service"] == error["wrong_value"] != body["service"]
            assert card["reference"]["decision"] == "reject"
            assert card["reference"]["reject_reason"] == "not_in_competence"
            added = [n["service"] for n in card["card"]["notified"] if n["status"] == "Добавлена"]
            assert added == [card["service"]]


def test_two_mistakes_are_told_by_different_sentences(type_rows: list[dict]) -> None:
    body = seed_card()
    body["card"]["description"] = "Течёт стояк холодной воды на лестничной клетке."
    card = distort.inject(
        body,
        count=2,
        rng=random.Random(1),  # noqa: S311
        ctx=context(type_rows),
        prefer=["house", "injured"],
    )
    told = card["card"]["description"][len(body["card"]["description"]) :].strip()
    sentences = [s for s in told.split(".") if s.strip()]
    assert len(sentences) == 2
    openers = {s.strip().split(" ")[0] for s in sentences}
    assert len(openers) == 2  # not the same pattern twice
    assert "пострадавших нет" in told and "дом 42" in told


def test_check_body_accepts_planted_cards_and_rejects_nonsense(
    type_rows: list[dict], classifier: dict
) -> None:
    refs = ReferenceCodes(
        incident_types={str(r["code"]): r for r in type_rows},
        flags={f["code"] for f in classifier["flags"]},
        services={s["code"] for s in classifier["services"]},
    )
    body = {**seed_card(), "difficulty": 3}
    rng = random.Random(7)  # noqa: S311
    planted = distort.inject(body, count=2, rng=rng, ctx=context(type_rows))
    assert len(planted["injected_errors"]) == 2
    assert check_body(planted, refs) == []
    broken = dict(planted)
    broken["injected_errors"] = [
        {"field": "address.house", "wrong_value": "42", "correct_value": "42"},
        {"field": "caller.email", "wrong_value": "a", "correct_value": "b"},
    ]
    problems = check_body(broken, refs)
    assert any("не отличается" in p for p in problems)
    assert any("Неизвестное поле" in p for p in problems)


async def test_template_provider_plants_mistakes_by_difficulty(type_rows: list[dict]) -> None:
    ctx = GenerationContext(type_rows=type_rows, catalogue=CATALOGUE, streets=STREETS)
    provider = TemplateGeneration()
    easy = await provider.generate(
        GenerationRequest(kind="card_response", phrase="течёт стояк в подъезде", difficulty=1),
        ctx,
    )
    assert not easy.body.get("injected_errors")
    hard = [
        await provider.generate(
            GenerationRequest(
                kind="card_response",
                phrase=f"течёт стояк в подъезде дома {i}",
                ticket_ref=f"t-{i}",
                difficulty=3,
            ),
            ctx,
        )
        for i in range(30)
    ]
    for result in hard:
        scenario = parse_scenario(result.body)
        assert len(scenario.injected_errors) == 2


def test_street_named_in_another_case_is_not_repeated(type_rows: list[dict]) -> None:
    body = seed_card()
    body["card"]["description"] = "Течёт стояк на лестнице в доме 42 на улице Свободы."
    card = distort.inject(
        body,
        count=1,
        rng=random.Random(3),  # noqa: S311
        ctx=context(type_rows),
        prefer=["street"],
    )
    assert card["injected_errors"][0]["field"] == "address.street"
    assert card["card"]["description"] == body["card"]["description"]


def _refused_card() -> dict:
    """The trainee's own service for an incident it does not serve: the reference refuses."""
    body = {**seed_card(), "service": "moslift"}
    body["reference"] = {
        "decision": "reject",
        "reject_reason": "not_in_competence",
        "status_chain": [{"status": "rejected", "comment_example": "Не в компетенции"}],
        "critical_errors": ["empty_reject_comment"],
    }
    return body


def test_planned_wrong_service_counts_as_one_of_the_mistakes(type_rows: list[dict]) -> None:
    for difficulty, others in ((2, 0), (3, 1)):
        body = distort.sent_to_wrong_service(
            {**_refused_card(), "difficulty": difficulty}, CATALOGUE
        )
        assert body["injected_errors"] == [
            {
                "field": "service",
                "wrong_value": "moslift",
                "correct_value": "rejected",
                "hint_level": 1,
                "wrong_label": "Мослифт",
                "correct_label": "Не принята: не в компетенции службы",
            }
        ]
        out = distort.inject_by_difficulty(
            body, type_rows=type_rows, catalogue=CATALOGUE, streets=STREETS, services=["moslift"]
        )
        fields = [e["field"] for e in out["injected_errors"]]
        assert fields[0] == "service" and len(fields) == 1 + others
        assert out["service"] == "moslift"  # the trainee's desk stays his own


def test_a_card_the_service_serves_is_not_marked(type_rows: list[dict]) -> None:
    body = {**seed_card(), "difficulty": 2}
    assert distort.sent_to_wrong_service(body, CATALOGUE) is body
