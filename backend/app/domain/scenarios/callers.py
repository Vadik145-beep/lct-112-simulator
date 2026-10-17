"""A caller for a card written from a phrase (issue #265): every card of АРМ-112 names who
called and from what number (the АОН), so a generated card gets a full name and a mobile number
when its source has none. Written the way the customer's tickets write them
(«Сидоров Иван Сергеевич, 916-126-34-71»); the choice is stable per card."""

from __future__ import annotations

import random
from dataclasses import dataclass

# (male, female) forms of a surname.
SURNAMES = (
    ("Сидоров", "Сидорова"),
    ("Кузнецов", "Кузнецова"),
    ("Морозов", "Морозова"),
    ("Волков", "Волкова"),
    ("Лебедев", "Лебедева"),
    ("Козлов", "Козлова"),
    ("Новиков", "Новикова"),
    ("Соколов", "Соколова"),
    ("Павлов", "Павлова"),
    ("Семёнов", "Семёнова"),
    ("Егоров", "Егорова"),
    ("Орлов", "Орлова"),
    ("Захаров", "Захарова"),
    ("Белов", "Белова"),
    ("Комаров", "Комарова"),
    ("Ильин", "Ильина"),
)
MALE_NAMES = ("Иван", "Алексей", "Сергей", "Андрей", "Дмитрий", "Михаил", "Олег", "Николай")
FEMALE_NAMES = ("Ольга", "Наталья", "Елена", "Татьяна", "Ирина", "Марина", "Светлана", "Анна")
# (male, female) forms of a patronymic.
PATRONYMICS = (
    ("Сергеевич", "Сергеевна"),
    ("Иванович", "Ивановна"),
    ("Петрович", "Петровна"),
    ("Александрович", "Александровна"),
    ("Николаевич", "Николаевна"),
    ("Викторович", "Викторовна"),
    ("Андреевич", "Андреевна"),
    ("Владимирович", "Владимировна"),
)
# Moscow mobile operators' codes as the tickets have them.
MOBILE_CODES = ("903", "905", "910", "915", "916", "917", "925", "926", "985", "999")


@dataclass(frozen=True)
class Caller:
    name: str
    phone: str
    gender: str  # male | female


def pick_caller(seed: str) -> Caller:
    rng = random.Random(seed)  # noqa: S311 - a stable choice per card, not security
    female = rng.random() < 0.5
    form = 1 if female else 0
    surname = rng.choice(SURNAMES)[form]
    first = rng.choice(FEMALE_NAMES if female else MALE_NAMES)
    patronymic = rng.choice(PATRONYMICS)[form]
    digits = [rng.randint(0, 9) for _ in range(7)]
    phone = (
        f"{rng.choice(MOBILE_CODES)}-{digits[0]}{digits[1]}{digits[2]}-"
        f"{digits[3]}{digits[4]}-{digits[5]}{digits[6]}"
    )
    return Caller(
        name=f"{surname} {first} {patronymic}",
        phone=phone,
        gender="female" if female else "male",
    )
