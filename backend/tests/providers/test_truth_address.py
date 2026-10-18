"""The address is checked against the truth (#263, documentation shoot 17.10.2026): on a card
where the operator of 112 put the wrong house in the field, the dispatcher who names the house
from the caller's words is right; the one who passes the field's house is sent all the same
and gets «Бригаде передан неверный адрес». And an officer does not ask for the incident once
the dispatcher has named it in the card's own words."""

from __future__ import annotations

from datetime import UTC, datetime

from app.dialog import officer
from app.domain.evaluation.card_response import evaluate_card_response
from app.domain.evaluation.schemas import CardResponseAttempt, CardResponseScenario
from app.domain.evaluation.service_call import true_card
from app.domain.scenarios import officers
from app.models import Attempt
from app.providers.dialog import CallerReply
from tests.domain.evaluation.helpers import card_scenario, grammar_ok, perfect_card_attempt
from tests.providers.test_chief_order import talk
from tests.providers.test_officer_misheard import new_call

NAME = "card_err_2_moek_dom"  # the field says дом 19, the caller says 17
TRUTH = "Зелёный проспект, дом 17, нет отопления во всех подъездах, пострадавших нет."
FIELD = "Зелёный проспект, дом 19, нет отопления во всех подъездах, пострадавших нет."
NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
ASK_INCIDENT = CallerReply(
    text="Что именно произошло? Какой тип происшествия по карточке?",
    topics=["incident_type"],
    operator_topics=[],
)


def card(difficulty: int = 1) -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": difficulty})


def errors_of(scenario: CardResponseScenario, attempt: Attempt) -> dict[str, str]:
    data = perfect_card_attempt(scenario).model_dump()
    data["service_calls"] = [
        {
            "service": c["service"],
            "kind": c["kind"],
            "started_at": NOW,
            "answered": True,
            "dialog": [
                {"role": t["role"], "text": t["text"], "topics": t["topics"]} for t in c["dialog"]
            ],
        }
        for c in attempt.service_calls
    ]
    result = evaluate_card_response(
        scenario, CardResponseAttempt.model_validate(data), grammar=grammar_ok()
    )
    return {e.code: e.explanation for e in result.errors}


def test_true_card_takes_the_house_from_the_caller() -> None:
    assert card().card.address.house == "19"
    assert true_card(card()).address.house == "17"


def test_the_true_house_sends_the_squad_without_a_mistake() -> None:
    scenario = card()
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    talk(scenario, attempt, call, TRUTH)
    assert talk(scenario, attempt, call, "Выезжайте.").startswith("Принял, выезжаем")
    errors = errors_of(scenario, attempt)
    assert "wrong_address_passed" not in errors
    assert "no_order_given" not in errors


def test_the_fields_house_sends_the_squad_and_is_named_as_a_mistake() -> None:
    scenario = card()
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    talk(scenario, attempt, call, FIELD)
    assert talk(scenario, attempt, call, "Выезжайте.").startswith("Принял, выезжаем")
    assert "дом 17" in errors_of(scenario, attempt)["wrong_address_passed"]


def test_the_truth_is_taken_as_said() -> None:
    """No misheard house any more (18.10.2026): the true address said, the squad goes."""
    scenario = card(2)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    assert not talk(scenario, attempt, call, TRUTH).startswith("Записал:")


def test_no_question_about_a_fact_already_named_in_the_cards_words() -> None:
    scenario = card_scenario("card_gkh_1_tech_v_podezde")
    body = scenario.card.model_copy(
        update={"description": "В вентиляционной шахте застряла кошка, мяукает третьи сутки."}
    )
    scenario = scenario.model_copy(update={"card": body})
    call = new_call("gkh")
    other = officer.officer_scenario(scenario, "gkh", "ЕДЦ ЖКХ", Attempt())
    said = "Улица Свободы, дом 42, корпус 2: в вентшахте застряла кошка."
    reply = officer.known_facts_reply(scenario, call, said, ASK_INCIDENT, other)
    assert reply.text != ASK_INCIDENT.text
    assert reply.topics != ["incident_type"]
    assert {"address", "incident_type"} <= set(reply.operator_topics)
    # Asked for what is still missing: the injured, then the access.
    assert reply.text in officers.FACT_REQUESTS


def test_the_head_takes_a_full_report_instead_of_asking_to_repeat() -> None:
    """A report to the head (#263): «Повторите» on a full report was the keyword table not
    knowing the words; the head confirms, and the facts of the report are counted."""
    scenario = card()
    head_call = {**new_call("moek"), "kind": officer.KIND_STAFF, "role": "head"}
    attempt = Attempt(service_calls=[head_call])
    head = officer.call_scenario(scenario, head_call, attempt)
    repeat = CallerReply(text="Повторите, не расслышал.", topics=["repeat"], operator_topics=[])
    report = "Докладываю: на Зелёном проспекте, дом 17, нет отопления во всех подъездах."
    reply = officer.report_taken_reply(scenario, head_call, report, repeat, head)
    assert reply.topics == ["confirm"]
    assert reply.text == "Понял, принял. Держите на контроле."
    # A short unclear word is still met with «повторите».
    assert officer.report_taken_reply(scenario, head_call, "Алло", repeat, head) is repeat
    assert officer.passes_facts(head_call)
