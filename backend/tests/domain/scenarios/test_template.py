"""Classifier ranking and template generation: every ticket becomes two valid bodies."""

from __future__ import annotations

import time

import pytest

from app.domain.evaluation.schemas import parse_scenario
from app.domain.scenarios.classify import rank_types, type_phrases
from app.domain.scenarios.facts import parse_ticket
from app.domain.scenarios.generated import TOPIC_CODES
from app.domain.scenarios.template import (
    ServiceInfo,
    build_call_intake,
    build_card_response,
    choose_card_service,
)
from app.importers.organizers import detect_traps
from tests.domain.scenarios.conftest import requires_seed, ticket

pytestmark = requires_seed


def _row(type_rows: list[dict], code: str) -> dict:
    return next(r for r in type_rows if r["code"] == code)


@pytest.mark.parametrize(
    ("ref", "expected_codes"),
    [
        ("1-1", {"1.1.1.1"}),  # пожар: мусор
        ("2-1", {"1.5.6.2"}),  # задымление: мусоропровод
        ("2-2", {"15.11.7.0"}),  # скандал в магазине
        ("2-3", {"17.7.7.0", "2.2.14.0"}),  # падение автомашины в воду
        ("3-2", {"15.10.1.0"}),  # нарушение тишины
        ("4-1", {"1.5.2.1"}),  # пожар: балкон
        ("15-1", {"1.1.5.1"}),  # пожар: лес
        ("29-1", {"15.13.6.0"}),  # подозрительный предмет
    ],
)
def test_rank_types_finds_the_row(
    tickets: list[dict], type_rows: list[dict], ref: str, expected_codes: set[str]
) -> None:
    item = ticket(tickets, ref)
    facts = parse_ticket(item["situation"], item["address"])
    injured = (
        True if facts.injured.startswith("есть") else (False if facts.injured == "нет" else None)
    )
    indoors = any([facts.address.entrance, facts.address.floor, facts.address.apartment])
    ranked = rank_types(facts.what_happened, type_rows, injured=injured, indoors=indoors, limit=1)
    assert ranked and ranked[0].code in expected_codes, [(c.code, c.title) for c in ranked]


@pytest.mark.parametrize(
    ("ref", "expected_code"),
    [
        ("13-2", "22.46.0.0"),  # рожает жена, воды отошли → Роды
        ("20-2", "22.35.0.0"),  # речь невнятная, лицо перекошено → Парализовало
        ("18-2", "22.2.0.0"),  # не может разбудить мужа, хрипы → Без сознания
        ("11-3", "17.2.1.3"),  # собирала грибы, заблудилась → Поиск в лесу
        ("17-2", "22.50.0.0"),  # судороги, пена изо рта → Судороги
        ("19-3", "17.4.11.0"),  # сбила электричка → Сбит поездом жд
        ("10-1", "1.6.16.1"),  # горит помещение кассы → Пожар: прочие объекты
    ],
)
def test_caller_wording_reaches_the_classifier_row(
    tickets: list[dict], type_rows: list[dict], ref: str, expected_code: str
) -> None:
    """The caller says «рожает, воды отошли», the classifier says «Роды»: without the wording
    dictionary the right row does not even get among the candidates the model chooses from."""
    item = ticket(tickets, ref)
    facts = parse_ticket(item["situation"], item["address"])
    ranked = rank_types(facts.what_happened, type_rows, limit=3)
    assert expected_code in [c.code for c in ranked], [(c.code, c.title) for c in ranked]


def test_every_wording_code_exists_in_the_classifier(type_rows: list[dict]) -> None:
    codes = {str(r["code"]) for r in type_rows}
    unknown = sorted(set(type_phrases()) - codes)
    assert not unknown, f"в data/seed/type_synonyms.json коды не из классификатора: {unknown}"


def test_every_ticket_gives_two_valid_bodies_fast(
    tickets: list[dict], type_rows: list[dict], catalogue: dict[str, ServiceInfo]
) -> None:
    started = time.perf_counter()
    for item in tickets:
        ref = f"{item['ticket_no']}-{item['item_no']}"
        facts = parse_ticket(item["situation"], item["address"])
        ranked = rank_types(facts.what_happened, type_rows, limit=1)
        row = _row(type_rows, ranked[0].code)
        traps = detect_traps(item["situation"], item["address"])
        call = build_call_intake(facts, row, ticket_ref=ref, traps=traps)
        card = build_card_response(facts, row, ticket_ref=ref, traps=traps, catalogue=catalogue)
        scenario = parse_scenario(call)
        assert scenario.kind == "call_intake"
        assert 12 <= len(scenario.replies) <= 25
        assert {r.topic for r in scenario.replies} <= set(TOPIC_CODES)
        assert not any(r.approved for r in scenario.replies)
        assert scenario.reference_card.incident_type == row["code"]
        assert scenario.reference_card.signs_path[0] == row["sign1"]
        assert "address" in scenario.required_topics
        card_scenario = parse_scenario(card)
        assert card_scenario.kind == "card_response"
        assert card_scenario.card.notified[-1].service == card_scenario.service
        assert card_scenario.reference.decision in {"accept", "reject"}
    # PRD plan, wave 8: a template scenario faster than two seconds — all 96 twice here.
    assert time.perf_counter() - started < 20


def test_traps_shape_the_call(tickets: list[dict], type_rows: list[dict]) -> None:
    item = ticket(tickets, "1-3")  # другой регион, звонит мама, описательный адрес
    facts = parse_ticket(item["situation"], item["address"])
    body = build_call_intake(
        facts,
        _row(type_rows, "22.53.0.0"),
        ticket_ref="1-3",
        traps=detect_traps(item["situation"], item["address"]),
    )
    assert body["caller"]["persona"] == "mother_anxious"
    assert "region" in body["required_topics"]
    assert body["reference_card"]["address"]["region"] == "Волгоградская область"
    region_reply = next(r for r in body["replies"] if r["topic"] == "region")
    assert "Волгоградская" in region_reply["text"]
    assert "регион" in body["caller"]["behaviour"]
    assert body["difficulty"] == 3


def test_dropped_call_scenario(tickets: list[dict], type_rows: list[dict]) -> None:
    item = ticket(tickets, "2-2")
    facts = parse_ticket(item["situation"], item["address"])
    body = build_call_intake(
        facts, _row(type_rows, "15.11.7.0"), ticket_ref="2-2", traps=["call_dropped"]
    )
    assert body["caller"]["drops_call"] is True
    assert body["caller"]["persona"] == "angry_customer"
    assert body["norm_seconds"] == 60
    assert any("бросает трубку" in r["text"] for r in body["replies"])


def test_other_region_card_is_rejected(
    tickets: list[dict], type_rows: list[dict], catalogue: dict[str, ServiceInfo]
) -> None:
    item = ticket(tickets, "1-3")
    facts = parse_ticket(item["situation"], item["address"])
    body = build_card_response(
        facts,
        _row(type_rows, "22.53.0.0"),
        ticket_ref="1-3",
        traps=["other_region"],
        catalogue=catalogue,
    )
    assert body["reference"]["decision"] == "reject"
    assert body["reference"]["reject_reason"] == "not_our_territory"
    assert body["reference"]["status_chain"][0]["status"] == "rejected"
    assert "empty_reject_comment" in body["reference"]["critical_errors"]


def test_card_service_prefers_arm112_dispatchers() -> None:
    catalogue = {
        "101": ServiceInfo("101", False),
        "102": ServiceInfo("102", False),
        "gkh": ServiceInfo("gkh", True),
        "territorial_oiv": ServiceInfo("territorial_oiv", True),
    }
    assert choose_card_service(["101", "gkh", "territorial_oiv"], catalogue, "101") == "gkh"
    assert choose_card_service(["102"], catalogue, "102") == "territorial_oiv"
