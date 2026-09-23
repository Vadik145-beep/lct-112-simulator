"""Обратный звонок диспетчера заявителю (ответ заказчика 23.09.2026): заявитель собирается
из карточки, а по испорченным оператором 112 полям называет правду, иначе ошибку в карточке
нечем вскрыть."""

from __future__ import annotations

from app.domain.evaluation.schemas import CardResponseScenario, InjectedError
from app.domain.scenarios import caller_back
from tests.domain.evaluation.helpers import card_scenario


def replies_by_topic(scenario: CardResponseScenario) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for reply in caller_back.callback_replies(scenario):
        result.setdefault(reply.topic, []).append(reply.text)
    return result


def with_error(scenario: CardResponseScenario, **error: str) -> CardResponseScenario:
    return scenario.model_copy(update={"injected_errors": [InjectedError(**error)]})


def test_caller_names_the_address_of_the_card() -> None:
    scenario = card_scenario()
    address = replies_by_topic(scenario)["address"][0]
    assert scenario.card.address.street in address
    assert scenario.card.address.house in address


def test_caller_corrects_the_house_the_operator_got_wrong() -> None:
    scenario = card_scenario()
    wrong = scenario.card.address.house
    spoiled = with_error(scenario, field="address.house", wrong_value=wrong, correct_value="199")
    address = replies_by_topic(spoiled)["address"][0]
    assert "199" in address
    assert f"дом {wrong}" not in address


def test_caller_corrects_the_injured_flag() -> None:
    scenario = card_scenario()
    spoiled = with_error(scenario, field="flags.injured", wrong_value="да", correct_value="нет")
    assert "Нет, пострадавших нет" in replies_by_topic(spoiled)["injured"][0]


def test_callback_scenario_asks_nothing_and_is_not_blocking() -> None:
    """Звонок заявителю не оценивается: обязательных тем нет и правило «мало вопросов» снято."""
    built = caller_back.callback_scenario(card_scenario())
    assert built.required_topics == []
    assert built.min_questions_share == 0.0
    assert built.caller.opening == caller_back.OPENING


def test_callback_scenario_tells_the_model_about_the_planted_mistake() -> None:
    spoiled = with_error(
        card_scenario(), field="address.house", wrong_value="12", correct_value="21"
    )
    facts = caller_back.callback_scenario(spoiled).caller.facts
    assert "address.house" in facts["в карточке ошиблись"]


def test_caller_keeps_two_answers_about_the_situation() -> None:
    """Второй вопрос «что там сейчас?» не должен получить ту же фразу, что и первый."""
    answers = replies_by_topic(card_scenario())["what_happened"]
    assert len(answers) == 2
    assert answers[0] != answers[1]


def test_cloud_gets_its_own_prompt_for_a_call_back() -> None:
    """Облаку нельзя давать промпт приёма вызова: там заявитель звонит сам, а здесь звонят
    ему (ответ заказчика 23.09.2026)."""
    from app.telephony.vapi import role_prompt

    built = caller_back.callback_scenario(card_scenario())
    assert built.caller.calls_back is True
    prompt = role_prompt(built)
    assert "ПЕРЕЗВОНИЛИ" in prompt
    assert "прямо сейчас звонит в службу 112" not in prompt
    # Номер карточки диспетчер не называет, и заявитель его не знает.
    assert "номеров карточки" in prompt.lower() or "номера карточки" in prompt.lower()
    # Неверные данные карточки заявитель поправляет, а не подтверждает.
    assert "поправь" in prompt.lower()


def test_spoken_reads_floors_and_entrances_as_ordinals() -> None:
    """«в 4 подъезде» вслух — «в четвёртом подъезде», иначе заявитель считает, а не говорит."""
    from app.providers.tts import spell_numbers

    def say(text: str) -> str:
        return spell_numbers(caller_back.spoken(text))

    assert say("В 4 подъезде темно") == "В четвёртом подъезде темно"
    assert say("на 3 этаж") == "на третий этаж"
    assert say("заявитель на 7 этаже") == "заявитель на седьмом этаже"
    assert say("у 1 подъезда") == "у первого подъезда"
    assert say("дом 14 к. 2") == "дом четырнадцать корпус два"
    # Мягкое склонение: «третьего», а не «третього».
    assert say("клетке 3 этажа") == "клетке третьего этажа"
    # Количество этажей в доме — не порядковое: «в доме 17 этажей».
    assert say("в доме 17 этажей") == "в доме семнадцать этажей"


def test_caller_does_not_dictate_the_number_he_is_called_on() -> None:
    """Диспетчер звонит ровно на этот номер — диктовать его обратно бессмысленно."""
    phone = next(
        text
        for topic, _tag, text in caller_back.callback_rows(card_scenario())
        if topic == "callback_phone"
    )
    assert "звоните" in phone
    assert not any(ch.isdigit() for ch in phone)
