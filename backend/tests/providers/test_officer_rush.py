"""The squad chief is in a hurry on difficulty 3 (issue #263): «Короче, что у вас?» on pick-up,
«Стоп, короче» once per call after a long phrase; the misheard house comes on the next short
phrase with the address."""

from __future__ import annotations

from app.dialog import officer
from app.domain.evaluation.schemas import CardResponseScenario
from app.domain.scenarios import misheard, rush
from app.models import Attempt
from tests.domain.evaluation.helpers import card_scenario
from tests.providers.test_officer_misheard import ADDRESS, NAME, new_call, talk

LONG = (
    "Добрый день, это диспетчер, у нас карточка от сто двенадцать, значит так, по адресу "
    "улица Свободы, дом 42, корпус 2, первый подъезд, третий этаж, затопление в подъезде, "
    "течёт стояк, жильцы волнуются, пострадавших нет, код домофона двенадцать к."
)


def hard() -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": 3})


def opening(scenario: CardResponseScenario, service: str, *, repeat: bool = False) -> str:
    built = officer.officer_scenario(scenario, service, "Служба", Attempt(), repeat=repeat)
    return built.caller.opening


def test_hurried_greeting_only_on_difficulty_three_and_the_own_service() -> None:
    assert opening(hard(), "gkh").endswith(rush.OPENING_TAIL)
    assert not opening(hard(), "moek").endswith(rush.OPENING_TAIL)
    assert not opening(hard(), "gkh", repeat=True).endswith(rush.OPENING_TAIL)
    medium = card_scenario(NAME).model_copy(update={"difficulty": 2})
    assert not opening(medium, "gkh").endswith(rush.OPENING_TAIL)


def test_long_phrase_is_cut_once_per_call() -> None:
    scenario = hard()
    call = new_call()
    attempt = Attempt(service_calls=[call])
    assert rush.is_long(LONG)
    assert talk(scenario, attempt, call, LONG) == rush.CUT_IN
    # No misheard house after the cut (18.10.2026), and the cut comes once per call.
    said = talk(scenario, attempt, call, ADDRESS)
    assert said is None or not said.startswith("Записал:")
    assert talk(scenario, attempt, call, LONG) != rush.CUT_IN
    assert misheard.state(attempt.service_calls) is None


def test_short_phrases_and_other_services_are_not_cut() -> None:
    scenario = hard()
    other = new_call(service="moek")
    attempt = Attempt(service_calls=[other])
    assert talk(scenario, attempt, other, LONG) is None
    medium = card_scenario(NAME).model_copy(update={"difficulty": 2})
    own = new_call()
    attempt = Attempt(service_calls=[own])
    assert officer.rush_reply(medium, own, LONG) is None
    assert officer.rush_reply(scenario, own, "Пострадавших нет.") is None
