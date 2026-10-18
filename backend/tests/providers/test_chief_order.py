"""The squad chief sends the squad only on the order of the dispatcher (#263, stand check
17.10.2026): no squad number and no «выезжаем» before it, «Так что делаем-то?» once the card
is told, «Принял, выезжаем. Наряд …» on «выезжайте» or «высылайте бригаду»; the misheard house
follows the difficulty of the lesson and the address as the card writes it."""

from __future__ import annotations

from types import SimpleNamespace

from app.dialog import officer
from app.domain.evaluation.schemas import CardResponseScenario
from app.domain.scenarios import officers
from app.models import Attempt
from app.providers.dialog import CallerReply
from tests.domain.evaluation.helpers import card_scenario
from tests.providers.test_officer_misheard import new_call

NAME = "card_moek_1_net_otopleniya"  # улица Молостовых, 10, к. 1; a card of difficulty 1
AS_IN_CARD = (
    "Авария, адрес: Россия, Москва, (ВАО, район Ивановское), улица Молостовых, 10, к. 1. "
    "Пострадавших нет."
)
CONFIRM = CallerReply(
    text="Информацию принял, направляю наряд 5600, бригада выезжает.",
    topics=["confirm"],
    operator_topics=[],
)
UNKNOWN = CallerReply(
    text="Это не ко мне, давайте по происшествию.", topics=["unknown"], operator_topics=[]
)


def talk(scenario, attempt, call, text, provider: CallerReply = CONFIRM) -> str:
    """One phrase answered as ``officer.say`` answers it, with ``provider`` for the model."""
    special = officer.special_reply(scenario, attempt, call, text)
    reply, extra = special if special is not None else (provider, {})
    reply = officer.chief_reply(scenario, attempt, call, text, reply)
    call["dialog"] = [
        *call["dialog"],
        {"role": "operator", "text": text, "topics": reply.operator_topics},
        {"role": "caller", "text": reply.text, "topics": reply.topics, **extra},
    ]
    attempt.service_calls = [c if c["id"] != call["id"] else call for c in attempt.service_calls]
    return reply.text


def moek(difficulty: int = 1) -> CardResponseScenario:
    return card_scenario(NAME).model_copy(update={"difficulty": difficulty})


def squad(scenario: CardResponseScenario) -> str:
    ref = officer.reference_for(scenario, scenario.service)
    return ref.order_number or officers.order_number(scenario, scenario.service)


def test_lesson_difficulty_wins_over_an_easier_card() -> None:
    card = moek(1)
    assert officer.lesson_scenario(card, SimpleNamespace(difficulty=2)).difficulty == 2
    assert officer.lesson_scenario(moek(3), SimpleNamespace(difficulty=1)).difficulty == 3


def test_the_address_is_never_misheard_on_purpose() -> None:
    """Decision of 18.10.2026: the bot never says the address wrong on purpose, at any
    difficulty; only the 112 operator's mistakes in the card stay."""
    for level in (1, 2, 3):
        scenario = officer.lesson_scenario(moek(1), SimpleNamespace(difficulty=level))
        for spoken in (AS_IN_CARD, "Молостовых, д. 10, к. 1", "улица Молостовых дом 10 корпус 1"):
            call = new_call("moek")
            attempt = Attempt(service_calls=[call])
            assert not talk(scenario, attempt, call, spoken).startswith("Записал:")


def test_the_chief_goes_once_he_knows_where_and_what() -> None:
    """Decision of 17.10.2026: enough information sends the squad, «выезжайте» alone does
    not."""
    scenario = moek(1)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    said = talk(scenario, attempt, call, "Высылайте бригаду на выезд.", provider=UNKNOWN)
    assert said == officer.ASK_ADDRESS
    assert officer.squad_state(attempt) == officers.SQUAD_STATE_WAITING
    said = talk(scenario, attempt, call, AS_IN_CARD + " Нет отопления в домах.")
    assert said == f"Принял, выезжаем. Наряд {squad(scenario)}."
    # Named in the call: on its way, no «выехали» call comes (18.10.2026).
    assert officer.squad_state(attempt) == "response_started"


def test_no_squad_number_while_something_is_missing() -> None:
    scenario = moek(1)
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    assert talk(scenario, attempt, call, "Пострадавших нет.") == officer.TAKEN
    said = talk(scenario, attempt, call, "Выезжайте.", provider=UNKNOWN)
    assert said == officer.ASK_ADDRESS
    said = talk(scenario, attempt, call, "Улица Молостовых, дом 10, корпус 1.")
    # The incident is still to come: no squad number yet.
    assert "наряд" not in said.lower() and "выезжаем" not in said.lower()


def test_the_address_said_with_a_pause_is_still_the_address() -> None:
    """Speech recognition cut the address at a pause on the stand (17.10.2026): «…Молостовых.»
    «10, корпус 1.» — the street and the house in two phrases."""
    scenario = officer.lesson_scenario(moek(1), SimpleNamespace(difficulty=2))
    call = new_call("moek")
    attempt = Attempt(service_calls=[call])
    talk(scenario, attempt, call, "Нет отопления, улица Молостовых.")
    said = talk(scenario, attempt, call, "Дом 10, корпус 1.")
    assert said == f"Принял, выезжаем. Наряд {squad(scenario)}."
