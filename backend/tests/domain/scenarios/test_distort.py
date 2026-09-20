"""Planted operator mistakes in generated cards (issue #35): share by difficulty, kinds, the
description keeping the truth, determinism, and the generation providers applying them."""

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


@pytest.mark.parametrize(("difficulty", "low", "high"), [(2, 0.15, 0.45), (3, 0.35, 0.65)])
def test_share_of_cards_with_mistakes_follows_difficulty(
    type_rows: list[dict], difficulty: int, low: float, high: float
) -> None:
    body = {**seed_card(), "difficulty": difficulty}
    n = 200
    hits = sum(
        bool(
            distort.inject_by_difficulty(
                {**body, "title": f"Карточка {i}"}, type_rows=type_rows, catalogue=CATALOGUE
            ).get("injected_errors")
        )
        for i in range(n)
    )
    assert low <= hits / n <= high


def test_planting_is_deterministic(type_rows: list[dict]) -> None:
    body = {**seed_card(), "difficulty": 3}
    first = distort.inject_by_difficulty(body, type_rows=type_rows, catalogue=CATALOGUE)
    second = distort.inject_by_difficulty(body, type_rows=type_rows, catalogue=CATALOGUE)
    assert first == second
    assert body.get("injected_errors") is None  # the input is not modified


def test_every_kind_keeps_the_truth_readable_and_validates(type_rows: list[dict]) -> None:
    ctx = context(type_rows)
    for kind, plant in distort.DISTORTIONS.items():
        # A missing service needs a card with several notified services.
        body = seed_card("card_moek_1_net_otopleniya" if kind == "service_missing" else None)
        card = dict(body)
        card["card"] = distort._copy_card(body["card"])
        error = plant(card, random.Random(kind), ctx)  # noqa: S311
        assert error is not None, kind
        scenario = parse_scenario({**card, "injected_errors": [error]})
        assert scenario.injected_errors[0].field == error["field"]
        assert error["wrong_value"] != error["correct_value"]
        if kind == "house":
            assert card["card"]["address"]["house"] != body["card"]["address"]["house"]
            assert "дом 42" in card["card"]["description"]
        elif kind == "street":
            assert card["card"]["address"]["street"] in {"улица Славы", "улица Свободная"}
            assert "улица Свободы" in card["card"]["description"]
        elif kind == "incident_type":
            assert card["card"]["incident_type"] != body["card"]["incident_type"]
            assert error["correct_label"]
        elif kind == "injured":
            assert card["card"]["flags"]["injured"] is True
            assert "Пострадавших нет" in card["card"]["description"]
        elif kind == "service_extra":
            assert error["wrong_value"].startswith("+")
            assert len(card["card"]["notified"]) == len(body["card"]["notified"]) + 1
        elif kind == "service_missing":
            assert error["wrong_value"].startswith("-")
            assert len(card["card"]["notified"]) == len(body["card"]["notified"]) - 1


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
    planted = [r.body for r in hard if r.body.get("injected_errors")]
    assert 5 <= len(planted) <= 25
    for body in planted:
        scenario = parse_scenario(body)
        assert scenario.injected_errors
