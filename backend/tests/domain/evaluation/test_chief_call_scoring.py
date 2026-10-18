"""The call to the squad chief scored by the weights of #263: the right people 25 %, the facts
30 %, the misheard house corrected 15 %, the order to go and the squad's number 15 %, the norm
15 %; and the two mistakes of the phone — no order given, the order given to the wrong person."""

from __future__ import annotations

import pytest

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.evaluation.service_call import gives_order, service_call_component
from app.domain.scenarios import misheard, no_vehicles, staff
from tests.domain.evaluation.helpers import at, card_scenario, grammar_ok, perfect_card_attempt

NAME = "card_gkh_1_tech_v_podezde"  # own service gkh, four facts, squad 1438
FACTS = "Улица Свободы, дом 42, корпус 2, течёт стояк, пострадавших нет, код подъезда 12К."
MAX = 20


def turn(role: str, text: str, s: float, topics: list[str] | None = None, **extra) -> dict:
    return {"role": role, "text": text, "topics": topics or [], "at": at(s), **extra}


def chief(*turns: dict, start: float = 40) -> dict:
    return {
        "service": "gkh",
        "kind": "outgoing",
        "started_at": at(start),
        "answered": True,
        "ended_at": at(start + 60),
        "dialog": [turn("caller", "Начальник аварийной бригады ЕДЦ ЖКХ, слушаю.", start), *turns],
    }


def attempt_of(scenario: CardResponseScenario, *calls: dict, number: bool = False):
    data = perfect_card_attempt(scenario).model_dump()
    if not number:
        for entry in data["status_log"]:
            entry["order_number"] = None
    data["service_calls"] = list(calls)
    return CardResponseAttempt.model_validate(data)


def fraction(scenario: CardResponseScenario, *calls: dict, number: bool = False) -> float:
    component = service_call_component(scenario, attempt_of(scenario, *calls, number=number), MAX)
    return component.score / MAX


def errors(scenario: CardResponseScenario, *calls: dict) -> dict[str, str]:
    result = evaluate_card_response(scenario, attempt_of(scenario, *calls), grammar=grammar_ok())
    return {e.code: e.explanation for e in result.errors}


def test_order_words() -> None:
    assert gives_order("Выезжайте, пожалуйста")
    assert gives_order("Направляйте бригаду на Свободы")
    assert gives_order("высылайте наряд")
    assert not gives_order("Пострадавших нет")


def test_everything_right_scores_in_full() -> None:
    scenario = card_scenario(NAME)
    call = chief(
        turn("operator", FACTS, 41),
        turn("operator", "Выезжайте.", 45),
        turn("caller", "Принял, наряд 1438, выезжаем.", 46),
    )
    assert fraction(scenario, call) == pytest.approx(1.0)
    assert "no_order_given" not in errors(scenario, call)


def test_no_order_loses_its_share_and_is_named() -> None:
    scenario = card_scenario(NAME)
    call = chief(turn("operator", FACTS, 41), turn("caller", "Принял.", 42))
    # 0.25 + 0.3 + 0.15 + 0 (no order, no number) + 0.15
    assert fraction(scenario, call) == pytest.approx(0.85)
    assert "Выезжайте" in errors(scenario, call)["no_order_given"]


def test_uncorrected_house_loses_its_share() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 2})
    call = chief(
        turn("operator", FACTS, 41),
        turn("caller", "Записал: улица Свободы, дом 44, корпус 2?", 42, [misheard.TOPIC_MISHEARD]),
        turn("operator", "Да, выезжайте.", 43),
        turn("caller", "Наряд 1438, выезжаем.", 44),
    )
    assert fraction(scenario, call) == pytest.approx(0.85)


def test_no_vehicles_needs_the_head_too() -> None:
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = chief(
        turn("operator", FACTS, 41),
        turn("operator", "Выезжайте.", 43),
        turn("caller", no_vehicles.ANNOUNCE, 44, [no_vehicles.TOPIC_NO_VEHICLES]),
        turn("operator", "Какой у вас наряд?", 45),
        turn("caller", "Наряд 1438.", 46),
    )
    # Half of the roles: the chief reached, the head never told.
    assert fraction(scenario, call) == pytest.approx(1.0 - 0.125)
    head = {
        "service": "gkh",
        "kind": "staff",
        "role": staff.ROLE_HEAD,
        "started_at": at(120),
        "answered": True,
        "ended_at": at(150),
        "dialog": [
            turn("operator", "У бригады нет машин.", 121),
            turn("caller", no_vehicles.RESOLVED, 122, [no_vehicles.TOPIC_VEHICLES_FOUND]),
        ],
    }
    assert fraction(scenario, call, head) == pytest.approx(1.0)


def test_order_to_the_head_is_the_wrong_contact() -> None:
    scenario = card_scenario(NAME)
    head_call = {"id": "h", "kind": "staff", "role": staff.ROLE_HEAD, "service": "gkh"}
    reply, _ = officer.wrong_contact_reply(head_call, "Высылайте бригаду на Свободы, 42")
    assert reply.text == staff.WRONG_CONTACT
    head = {
        "service": "gkh",
        "kind": "staff",
        "role": staff.ROLE_HEAD,
        "started_at": at(30),
        "answered": True,
        "ended_at": at(40),
        "dialog": [
            turn("operator", "Высылайте бригаду на Свободы, 42", 31),
            turn("caller", reply.text, 32, reply.topics),
        ],
    }
    good = chief(turn("operator", FACTS, 41), turn("operator", "Выезжайте.", 45))
    assert "«руководитель организации»" in errors(scenario, head, good)["wrong_contact"]
    assert officer.wrong_contact_reply(head_call, "Докладываю: течь на Свободы") is None
    assert officer.wrong_contact_reply({"kind": "outgoing"}, "Выезжайте") is None


def test_no_order_is_not_blamed_when_the_chief_has_no_vehicles() -> None:
    """Stand 18.10.2026, card 08978084 (decision of the user): the chief said «машин нет», the
    head of the organization gave the vehicle — there was nobody to give the order to."""
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = chief(
        turn("operator", FACTS, 41),
        turn("caller", no_vehicles.ANNOUNCE, 44, [no_vehicles.TOPIC_NO_VEHICLES]),
        turn("operator", "И что делать?", 45),
    )
    head = {
        "service": "gkh",
        "kind": "staff",
        "role": staff.ROLE_HEAD,
        "started_at": at(120),
        "answered": True,
        "ended_at": at(150),
        "dialog": [
            turn("operator", "У бригады нет машин.", 121),
            turn("caller", no_vehicles.RESOLVED, 122, [no_vehicles.TOPIC_VEHICLES_FOUND]),
        ],
    }
    assert "no_order_given" not in errors(scenario, call, head)
    # The order loses nothing either; the number is still to be taken.
    assert fraction(scenario, call, head) == pytest.approx(1.0 - 0.075)
    component = service_call_component(scenario, attempt_of(scenario, call, head), MAX)
    assert component.items[0]["order_given"] is False  # the review says what was said


def test_no_order_on_a_card_turned_to_no_vehicles_before_the_chief_said_it() -> None:
    """The turn is fixed on the first call to the chief: the card goes the «машин нет» way even
    when the record lost the chief's words."""
    scenario = card_scenario(NAME).model_copy(update={"difficulty": 3})
    call = chief(turn("operator", FACTS, 41), turn("caller", "Принял.", 42))
    assert "no_order_given" in errors(scenario, call)
    planned = {**call, "squad_plan": no_vehicles.PLAN_NO_VEHICLES}
    assert "no_order_given" not in errors(scenario, planned)
