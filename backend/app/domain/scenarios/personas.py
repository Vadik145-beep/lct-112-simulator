"""Caller personas and background noises a scenario can name (PRD 9.3 ``caller.persona``,
``caller.noise``). Codes are ours; titles are shown in the teacher's form; ``style`` goes into
the generation prompt and shapes the template replies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Persona:
    code: str
    title: str
    voice: str  # default voice id (app.providers.tts.VOICES)
    style: str  # how the caller talks, for the model and the templates
    interjection: str  # a short filler the template puts before some replies


PERSONAS: list[Persona] = [
    Persona("calm", "Спокойный очевидец", "ru_male_1", "говорит ровно, отвечает по делу", ""),
    Persona(
        "worried_resident",
        "Встревоженный жилец",
        "ru_female_1",
        "волнуется, торопит, но отвечает на вопросы",
        "Ой, ",
    ),
    Persona(
        "elderly_calm",
        "Пожилой человек",
        "ru_male_3",
        "говорит медленно, переспрашивает, путается в мелочах",
        "Сейчас, сейчас... ",
    ),
    Persona(
        "elderly_panicked",
        "Пожилой человек в панике",
        "ru_female_2",
        "испуган, сбивается, повторяет одно и то же",
        "Господи, ",
    ),
    Persona(
        "mother_anxious",
        "Встревоженная мать",
        "ru_female_3",
        "говорит быстро, беспокоится о ребёнке, просит скорее приехать",
        "Пожалуйста, скорее, ",
    ),
    Persona(
        "witness_shaken",
        "Потрясённый свидетель",
        "ru_male_4",
        "в шоке, говорит обрывками, детали вспоминает не сразу",
        "Я... ",
    ),
    Persona(
        "witness_urgent",
        "Очевидец, торопит",
        "ru_male_2",
        "говорит рублеными фразами, торопит, требует выехать быстрее",
        "Скорее, ",
    ),
    Persona(
        "victim_panicked",
        "Пострадавший в панике",
        "ru_female_2",
        "сам пострадал, говорит через боль, срывается на крик",
        "Ой, ",
    ),
    Persona(
        "calm_commuter",
        "Спокойный пассажир",
        "ru_male_3",
        "говорит буднично, как о дорожной заминке, деталей не драматизирует",
        "",
    ),
    Persona(
        "angry_customer",
        "Раздражённый заявитель",
        "ru_male_2",
        "резкий, перебивает, считает вопросы лишними",
        "Да что вы спрашиваете, ",
    ),
    Persona(
        "child",
        "Ребёнок",
        "ru_child_1",
        "ребёнок 8-12 лет, простые слова, не знает точного адреса, зовёт взрослых",
        "",
    ),
    Persona(
        "drunk",
        "Нетрезвый заявитель",
        "ru_male_2",
        "речь невнятная, сбивается на посторонние темы, повторяет вопрос",
        "Э-э... ",
    ),
]

PERSONA_BY_CODE: dict[str, Persona] = {p.code: p for p in PERSONAS}
DEFAULT_PERSONA = "calm"

NOISES: list[tuple[str, str]] = [
    ("indoor", "В помещении (тихо)"),
    ("street", "Улица"),
    ("crowd", "Толпа, шум"),
]
NOISE_CODES: set[str] = {code for code, _ in NOISES}
DEFAULT_NOISE = "indoor"


def persona(code: str | None) -> Persona:
    return PERSONA_BY_CODE.get(code or DEFAULT_PERSONA, PERSONA_BY_CODE[DEFAULT_PERSONA])


# The same manner of speaking in the other gender: normal ↔ normal, slow ↔ slow, fast ↔ fast.
# The child's voice has no counterpart and stays as it is.
COUNTERPART_VOICE: dict[str, str] = {
    "ru_male_1": "ru_female_1",
    "ru_male_2": "ru_female_1",
    "ru_male_3": "ru_female_2",
    "ru_male_4": "ru_female_3",
    "ru_female_1": "ru_male_1",
    "ru_female_2": "ru_male_3",
    "ru_female_3": "ru_male_4",
}


def voice_for(code: str | None, gender: str | None) -> str:
    """The persona's voice, switched to the gender of the caller the ticket names: a man must
    not answer in a woman's voice just because the persona's default is female."""
    voice = persona(code).voice
    if gender == "male" and voice.startswith("ru_female"):
        return COUNTERPART_VOICE[voice]
    if gender == "female" and voice.startswith("ru_male"):
        return COUNTERPART_VOICE[voice]
    return voice
