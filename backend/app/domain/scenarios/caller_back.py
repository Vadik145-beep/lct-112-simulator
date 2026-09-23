"""Обратный звонок диспетчера ДДС заявителю (ответ заказчика 23.09.2026: «при необходимости
диспетчер ДДС может напрямую выйти на заявителя по обычному телефону, т.к. номер заявителя
есть уже в карточке, и далее общаться с ним минуя 112»).

Заявителя играет тот же движок диалога, что и в приёме вызова: профиль плюс банк реплик по
темам (``CALLER_TOPICS``), поэтому select / hybrid / generate и голосовой тракт работают без
изменений. Реплики собираются из карточки, но поля, в которых ошибся оператор 112
(``injected_errors``), заявитель называет ПРАВИЛЬНО: иначе ошибку в карточке нечем вскрыть.

Заказчик отдельно отметил: номер карточки при дозвоне заявителю диспетчер не называет,
разговор идёт по сути заявления, поэтому этого нет ни в приветствии, ни в поведении.
"""

from __future__ import annotations

import re

from app.domain.evaluation.schemas import (
    CallerProfile,
    CallIntakeScenario,
    Card,
    CardResponseScenario,
    ReferenceCard,
    Reply,
)
from app.domain.scenarios.facts import caller_gender

# Ids of the built-in replies; a scenario adds none of its own yet, but the base keeps the
# numbering apart from the officers' bank (``officers.BUILTIN_REPLY_ID_BASE``).
REPLY_ID_BASE = 2000

PERSONA = "worried_resident"
# Заявителя выдернули звонком через несколько минут после происшествия: он ещё взволнован,
# но острая фаза прошла. Голоса — быстрые, «взволнованные» слоты, а не спокойные дикторские
# (решение пользователя 24.09.2026); студийные записи пишутся Vladislav и Lesia.
VOICE_MALE = "ru_male_4"
VOICE_FEMALE = "ru_female_3"
OPENING = "Алло? Кто это?"
BEHAVIOUR = (
    "заявитель, которому перезвонили из дежурной службы: отвечает на вопросы, говорит то, что "
    "видит сам, номер карточки не называет и не спрашивает, о самом происшествии знает только "
    "то, что видел на месте"
)
# Теги eleven_v3 для студийной записи (scripts/voice_replies.py). Тревога, а не паника:
# коммунальное ЧП, заявитель не пострадавший (решение пользователя 24.09.2026).
DEFAULT_TAG = "[nervous]"
TAGS: dict[str, str] = {
    "what_happened": "[anxious]",
    "address": "[anxious]",
    "injured": "[nervous]",
    "danger": "[anxious]",
    "repeat": "[urgent]",
    "unknown": "[nervous]",
    "tired": "[tired]",
}
# Field paths of the card an injected error may correct (``InjectedError.field``).
FIELD_STREET = "address.street"
FIELD_HOUSE = "address.house"
FIELD_BUILDING = "address.building"
FIELD_ENTRANCE = "address.entrance"
FIELD_FLOOR = "address.floor"
FIELD_APARTMENT = "address.apartment"
FIELD_INJURED = "flags.injured"
FIELD_CALLER_NAME = "caller.name"
FIELD_CALLER_PHONE = "caller.phone"
FIELD_DESCRIPTION = "description"

TRUE_WORDS = {"true", "1", "да", "есть"}


def corrections(scenario: CardResponseScenario) -> dict[str, str]:
    """Field → what the caller actually says, for every mistake planted in the card."""
    return {e.field: e.correct_value for e in scenario.injected_errors}


def _truth(card: Card, fixes: dict[str, str], field: str, fallback: str | None) -> str:
    """The value the caller names: the correction when the field was spoiled, else the card."""
    if field in fixes:
        return str(fixes[field]).strip()
    return str(fallback or "").strip()


def _house_phrase(card: Card, fixes: dict[str, str]) -> str:
    house = _truth(card, fixes, FIELD_HOUSE, card.address.house)
    building = _truth(card, fixes, FIELD_BUILDING, card.address.building)
    if house and building:
        return f"дом {house}, корпус {building}"
    if house:
        return f"дом {house}"
    return ""


def address_reply(card: Card, fixes: dict[str, str]) -> str:
    street = _truth(card, fixes, FIELD_STREET, card.address.street)
    house = _house_phrase(card, fixes)
    if street and house:
        return f"{street}, {house}."
    if street:
        return f"{street}."
    if house:
        return f"{house}."
    return card.address.descriptive or "Точного адреса не знаю, скажу, что вижу вокруг."


def entrance_reply(card: Card, fixes: dict[str, str]) -> str:
    parts = []
    entrance = _truth(card, fixes, FIELD_ENTRANCE, card.address.entrance)
    floor = _truth(card, fixes, FIELD_FLOOR, card.address.floor)
    apartment = _truth(card, fixes, FIELD_APARTMENT, card.address.apartment)
    if entrance:
        parts.append(f"подъезд {entrance}")
    if floor:
        parts.append(f"этаж {floor}")
    if apartment:
        parts.append(f"квартира {apartment}")
    if not parts:
        # В карточке подъезда нет: на улице его и не бывает, а в доме это «по всему дому».
        return "Подъезд назвать не могу, это не в квартире."
    return ", ".join(parts).capitalize() + "."


def injured_reply(card: Card, fixes: dict[str, str]) -> str:
    on_card = "да" if card.flags.get("injured") else "нет"
    raw = _truth(card, fixes, FIELD_INJURED, on_card)
    if raw.lower() in TRUE_WORDS:
        return "Да, пострадавшие есть, им нужна помощь."
    return "Нет, пострадавших нет, все целы."


def past(female: bool, stem: str) -> str:
    """Глагол прошедшего времени под пол заявителя: «видел» — «видела». Восемь из восемнадцати
    карточек с женщинами, и мужская форма в женском голосе слышна сразу."""
    return f"{stem}а" if female else stem


def _name_reply(card: Card, fixes: dict[str, str], female: bool) -> str:
    name = _truth(card, fixes, FIELD_CALLER_NAME, card.caller.name)
    if name:
        return f"{name}."
    return (
        f"Фамилию называть не буду, я просто мимо {past(female, 'шёл') if not female else 'шла'}."
    )


def _phone_reply(card: Card, fixes: dict[str, str], female: bool) -> str:
    """Диктовать номер не надо: диспетчер звонит ровно на него (замечание пользователя
    24.09.2026). Заявитель это и говорит."""
    called = past(female, "звонил")
    return f"Так вы на него и звоните, я с него и {called}. Другого у меня нет."


# Числа в описании карточки записаны цифрами, а вслух этаж и подъезд порядковые: «в 4 подъезде»
# читается «в четвёртом подъезде», а не «в четыре подъезде» (замечание пользователя 24.09.2026).
_NOMINATIVE = ""
# «третий» склоняется мягко: третьего, третьем — не «третього».
_SOFT_ENDINGS = {"ом": "ем", "ого": "его"}
_ORDINAL_UNITS = ("этаж", "подъезд")


def _ordinal(number: int, ending: str) -> str:
    """«третий» в именительном и «третьем» в предложном: склоняем только когда нужно, иначе
    из «третий» получилось бы «третый»."""
    from num2words import num2words

    word = num2words(number, lang="ru", to="ordinal")
    if ending == _NOMINATIVE:
        return word
    for tail in ("ый", "ой", "ий"):
        if word.endswith(tail):
            stem = word[: -len(tail)]
            if tail == "ий":  # третий → третьего, третьем
                return f"{stem}ь{_SOFT_ENDINGS[ending]}"
            return f"{stem}{ending}"
    return word


def _ordinalize(text: str) -> str:
    """«на 3 этаже» → «на третьем этаже», «у 1 подъезда» → «у первого подъезда»."""

    def replace(match: re.Match[str]) -> str:
        number, space, unit, tail = match.groups()
        if tail in ("е", "ах"):  # этаже, подъезде, домах
            ending = "ом"
        elif tail == "а":  # этажа, подъезда
            ending = "ого"
        else:
            ending = _NOMINATIVE
        return f"{_ordinal(int(number), ending)}{space}{unit}{tail}"

    pattern = rf"(\d+)(\s*)({'|'.join(_ORDINAL_UNITS)})(е|а|ах|)\b"
    return re.sub(pattern, replace, text)


def spoken(text: str) -> str:
    """Текст, как его произносят: сокращения раскрываются, этаж и подъезд становятся
    порядковыми, дефисы номера не мешают числам стать словами."""
    text = re.sub(r"\bк\.\s*(?=\d)", "корпус ", text)
    text = re.sub(r"\bстр\.\s*(?=\d)", "строение ", text)
    text = re.sub(r"\bкв\.\s*(?=\d)", "квартира ", text)
    text = _ordinalize(text)
    return text.replace("+7", "7").replace("-", " ").replace("  ", " ")


def _role_reply(card: Card, female: bool) -> str:
    role = (card.caller.role or "").strip()
    return (
        f"Я {role}."
        if role
        else f"Я просто рядом {past(female, 'оказался' if not female else 'оказалась')}."
    )


def _situation_reply(card: Card, fixes: dict[str, str]) -> str:
    """Первое предложение описания: реплика идёт вслух, а описание карточки бывает на три
    предложения — вслух это звучит как зачитывание протокола."""
    text = _truth(card, fixes, FIELD_DESCRIPTION, card.description)
    if not text:
        return "Всё как я и говорил."
    # Точка сокращения («дом 14 к. 2») концом предложения не считается: следом за настоящей
    # точкой идёт заглавная буква.
    head = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁ])", text.strip())[0]
    return head or text


def callback_rows(
    scenario: CardResponseScenario, *, arrived: bool = False
) -> list[tuple[str, str, str]]:
    """(topic, tag, text) of everything the caller says on a call back. Within a topic the
    engine takes the first unused reply, so «что случилось» answers first about the incident
    and then about what is going on right now — the dispatcher's second question does not get
    the same phrase twice. ``arrived`` is the squad's state: пока никого нет или уже работают.
    """
    card = scenario.card
    fixes = corrections(scenario)
    female = caller_gender(card.caller.name, card.caller.role) == "female"
    seen_verb = past(female, "увидел")
    on_scene = (
        "Приехали, уже работают, я их вижу."
        if arrived
        else f"Пока никого нет, я бы {seen_verb}, я тут стою."
    )
    rows: list[tuple[str, str]] = [
        ("what_happened", _situation_reply(card, fixes)),
        (
            "what_happened",
            f"Пока ничего не изменилось, помощи на месте ещё не {past(female, 'видел')}.",
        ),
        ("address", address_reply(card, fixes)),
        ("entrance_floor_code", entrance_reply(card, fixes)),
        ("injured", injured_reply(card, fixes)),
        ("danger", "Людям ничего не угрожает, все стоят в стороне."),
        ("count_people", "Тут несколько человек рядом."),
        ("time", f"Началось незадолго до того, как я {past(female, 'позвонил')} в сто двенадцать."),
        ("caller_name", _name_reply(card, fixes, female)),
        ("caller_role", _role_reply(card, female)),
        ("callback_phone", _phone_reply(card, fixes, female)),
        ("repeat", f"Повторяю: {address_reply(card, fixes)}"),
        # «Приехали?» — самый частый вопрос диспетчера, а отдельной темы под него в словаре
        # заявителя нет. Без модели движок берёт первую неиспользованную реплику темы, поэтому
        # ответ о бригаде стоит первым среди «вне темы» (живой прогон 24.09.2026).
        ("unknown", on_scene),
        ("unknown", "Этого я не знаю, я вижу только то, что происходит на месте."),
        # Реплики самого обратного звонка: человека выдернули звонком с незнакомого номера,
        # он уже звонил в сто двенадцать и ждёт помощь (правки пользователя 24.09.2026).
        ("unknown", "А вы откуда звоните? Я номер не знаю."),
        ("unknown", f"Я же уже {past(female, 'вызывал')}, зачем опять спрашиваете?"),
        ("repeat", "Алло, вы меня слышите? Плохо слышно."),
        ("tired", "Мне сейчас неудобно говорить, перезвоните попозже."),
        ("tired", f"Да я всё уже {past(female, 'рассказал')}, когда {past(female, 'звонил')}."),
    ]
    return [(topic, TAGS.get(topic, DEFAULT_TAG), text) for topic, text in rows]


def callback_replies(scenario: CardResponseScenario, *, arrived: bool = False) -> list[Reply]:
    """The caller's bank as the dialog engine sees it. «tired» is not a topic of the caller's
    vocabulary: such a phrase is an answer to anything, so it goes under «unknown»."""
    return [
        Reply(
            id=REPLY_ID_BASE + i,
            topic="unknown" if topic == "tired" else topic,
            text=text,
            approved=True,
        )
        for i, (topic, _tag, text) in enumerate(callback_rows(scenario, arrived=arrived))
    ]


def _reference_card(scenario: CardResponseScenario) -> ReferenceCard:
    card = scenario.card
    return ReferenceCard(
        incident_type=card.incident_type,
        signs_path=list(card.signs),
        flags=dict(card.flags),
        address=card.address,
        caller=card.caller,
        description=card.description,
        expected_services=[n.service for n in card.notified],
    )


def facts_of(scenario: CardResponseScenario, *, arrived: bool = False) -> dict[str, str]:
    """What the caller knows, for the generating providers and for the cloud prompt."""
    card = scenario.card
    fixes = corrections(scenario)
    on_scene = "бригада на месте, работает" if arrived else "на месте пока никого из служб не видно"
    facts = {
        "кто вы": card.caller.role or "заявитель",
        "что случилось": _situation_reply(card, fixes),
        "адрес": address_reply(card, fixes),
        "пострадавшие": injured_reply(card, fixes),
        "приехали ли": on_scene,
        "номер карточки": "не знает и не спрашивает: разговор идёт по сути заявления",
    }
    wrong = ", ".join(sorted(fixes)) if fixes else ""
    if wrong:
        facts["в карточке ошиблись"] = (
            f"оператор 112 записал неверно ({wrong}); на вопрос диспетчера нужно назвать то, "
            "как на самом деле, и поправить его"
        )
    return facts


def caller_name(scenario: CardResponseScenario) -> str:
    fixes = corrections(scenario)
    return _truth(scenario.card, fixes, FIELD_CALLER_NAME, scenario.card.caller.name) or "Заявитель"


def voice_of(scenario: CardResponseScenario) -> str:
    """Голос заявителя: взволнованный, по полу из карточки."""
    gender = caller_gender(scenario.card.caller.name, scenario.card.caller.role)
    return VOICE_FEMALE if gender == "female" else VOICE_MALE


def callback_scenario(
    scenario: CardResponseScenario, *, arrived: bool = False
) -> CallIntakeScenario:
    """The caller of the card as the dialog engine sees him on a call back: the same shape as
    a call-intake scenario, so the providers, the voice and the transcript work unchanged.
    ``arrived`` is the squad's state: it decides whether help is already on the scene."""
    return CallIntakeScenario(
        kind="call_intake",
        title=f"Звонок заявителю: {caller_name(scenario)}",
        ticket_ref=scenario.ticket_ref,
        difficulty=scenario.difficulty,
        caller=CallerProfile(
            persona=PERSONA,
            voice=voice_of(scenario),
            noise=None,
            opening=OPENING,
            facts=facts_of(scenario, arrived=arrived),
            behaviour=BEHAVIOUR,
            calls_back=True,
        ),
        replies=callback_replies(scenario, arrived=arrived),
        # Ничего спрашивать не обязан: звонок заявителю не оценивается (решение 23.09.2026),
        # блокирующее правило «мало вопросов» к нему тоже не применяется.
        required_topics=[],
        min_questions_share=0.0,
        reference_card=_reference_card(scenario),
    )


__all__ = [
    "callback_replies",
    "callback_scenario",
    "caller_name",
    "corrections",
]
