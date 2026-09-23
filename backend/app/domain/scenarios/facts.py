"""Fact sheet of a ticket situation (PRD 8.3): who calls, from which number, what happened,
who is hurt and where, parsed from the two free-text columns of the organizers' tickets.

The tickets were written by hand for exams, so the parser is a set of patterns tuned on the 96
situations, not a general address parser. Whatever it cannot place stays in ``what_happened``
or ``address.descriptive`` and is reviewed by the teacher (drafts are created in ``review``).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.domain.evaluation.schemas import Address

# --- phone -----------------------------------------------------------------------------------

_PHONE = re.compile(r"(?:\+?7|8)?[\s(]*(9\d{2})\)?[\s-]*(\d{3})[\s-]*(\d{2})[\s-]*(\d{2})")


def find_phone(text: str) -> tuple[str | None, str]:
    """(formatted phone, text without it). Mobile numbers only: that is what tickets use."""
    match = _PHONE.search(text)
    if not match:
        return None, text
    phone = "-".join(match.groups())
    return phone, (text[: match.start()] + " " + text[match.end() :]).strip(" ,.;")


# --- caller ----------------------------------------------------------------------------------

_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_PATRONYMIC = r"[А-ЯЁ][а-яё]+(?:вич|вна|ич|чна|ьич)"
_FULL_NAME = re.compile(rf"\b({_WORD}) ({_WORD}) ({_PATRONYMIC})\b")
_SHORT_NAME = re.compile(rf"\b({_WORD}) ({_WORD})\b")
_CALLED_BY = re.compile(
    r"(?:вызывает|звонит|сообщает)\s+(мама|мать|папа|отец|супруг|супруга|муж|жена|брат|сестра|"
    r"дочь|сын|бабушка|дедушка|сосед|соседка|подруга|друг|прохожий|кассир|администратор|"
    r"охранник|водитель|учитель|воспитатель|старшая по подъезду|ребёнок|ребенок|подросток|"
    r"пожилой человек|пожилая женщина|пенсионер|пенсионерка|очевидец|пострадавший)",
    re.IGNORECASE,
)
_ROLE_IN_BRACKETS = re.compile(r"\(([^)]{3,40})\)")
_RELATIVES = {
    "мама",
    "мать",
    "папа",
    "отец",
    "супруг",
    "супруга",
    "муж",
    "жена",
    "брат",
    "сестра",
    "дочь",
    "сын",
    "бабушка",
    "дедушка",
}
# Capitalized words that look like a name but are not one.
_NOT_NAMES = {
    "Москва",
    "Мегафон",
    "Ваз",
    "Донормил",
    "Бургер",
    "Кинга",
    "Открыть",
    "Вызывает",
    "Потеря",
    "Открытого",
    "Иномарка",
    "Двое",
    "Соседи",
    "Плохо",
    "Видит",
    "Горит",
    "Неизвестные",
    "Мужчина",
    "Женщина",
    "Ребенок",
    "Ребёнок",
    "Подросток",
    "Дерутся",
    "Падение",
    "Задымление",
    "Возгорание",
    "Поругался",
    "Громко",
    "Сожитель",
    "Пострадавших",
    "Кровотечение",
    "Тверской",
    "Ярославском",
    "Комсомольская",
    "Киевская",
    "Пассажирская",
    "Симферопольское",
}


@dataclass
class CallerFacts:
    name: str | None = None
    phone: str | None = None
    role: str = "очевидец"  # очевидец | участник | пострадавший | родственник
    relation: str | None = None  # «мама», «супруг» when the role is a relative
    gender: str | None = None  # male | female, by the patronymic or the relative word


def _names(text: str) -> list[str]:
    found: list[str] = []
    for match in _FULL_NAME.finditer(text):
        if match.group(1) in _NOT_NAMES:
            continue
        found.append(" ".join(match.groups()))
    if found:
        return found
    for match in _SHORT_NAME.finditer(text):
        first, second = match.groups()
        if first in _NOT_NAMES or second in _NOT_NAMES:
            continue
        # «Смирнов Илья», «Петров Олег»: surname + given name, both declinable Russian words.
        if second.endswith(("ов", "ев", "ин", "ий", "ая", "ое")) or len(second) < 3:
            continue
        found.append(f"{first} {second}")
    return found


# Who is on the line: the patronymic is the reliable sign, the relative word the next one.
# The voice of the scenario follows it, so «Ивлев Артем Олегович» does not answer as a woman.
_MALE_PATRONYMIC = re.compile(r"\b[А-ЯЁ][а-яё]+(?:ович|евич|ьич)\b")
_FEMALE_PATRONYMIC = re.compile(r"\b[А-ЯЁ][а-яё]+(?:овна|евна|инична|ична)\b")
_MALE_WORDS = ("муж", "супруг", "отец", "папа", "брат", "сын", "дедушка", "дед", "сожитель")
_FEMALE_WORDS = ("жена", "супруга", "мама", "мать", "сестра", "дочь", "бабушка", "соседка")


def _gender_by_word(text: str | None) -> str | None:
    """«супруга» before «супруг», «мать» before «муж»: the longer word wins."""
    word = (text or "").lower().strip()
    if not word:
        return None
    if any(word.startswith(w) for w in _FEMALE_WORDS):
        return "female"
    if any(word.startswith(w) for w in _MALE_WORDS):
        return "male"
    return None


def caller_gender(name: str | None, relation: str | None) -> str | None:
    if name:
        if _FEMALE_PATRONYMIC.search(name):
            return "female"
        if _MALE_PATRONYMIC.search(name):
            return "male"
    # Имя в карточке бывает описанием, а не ФИО: «Соседка с 9 этажа» — тоже женщина
    # (замечание пользователя 24.09.2026, карточка card_2-1_dubl).
    return _gender_by_word(name) or _gender_by_word(relation)


def parse_caller(situation: str) -> CallerFacts:
    phone, rest = find_phone(situation)
    caller = CallerFacts(phone=phone)
    lowered = rest.lower()

    called_by = _CALLED_BY.search(rest)
    bracket_role = next(
        (
            m.group(1).strip()
            for m in _ROLE_IN_BRACKETS.finditer(rest)
            if not re.search(r"\d", m.group(1))
            and any(
                w in m.group(1).lower()
                for w in ("очевидец", "прохожий", "старшая", "сосед", "водитель", "охранник")
            )
        ),
        None,
    )
    names = _names(rest)
    if called_by:
        who = called_by.group(1).lower()
        after = _names(rest[called_by.end() :])
        caller.name = after[0] if after else None
        if who in _RELATIVES:
            caller.role, caller.relation = "родственник", who
        elif who == "пострадавший":
            caller.role, caller.relation = "пострадавший", None
        else:
            caller.role, caller.relation = "очевидец", who
    else:
        caller.name = names[-1] if names else None
        if bracket_role:
            caller.role = "очевидец"
            caller.relation = bracket_role
        elif re.search(r"\b(поругался|у меня|мне |меня |моя|мой|нас )", lowered):
            caller.role = "участник"
        elif re.search(r"(наблюда|видит|проезжал|прохож|очевидец|заявитель)", lowered):
            caller.role = "очевидец"
    caller.gender = caller_gender(caller.name, caller.relation)
    return caller


# --- injured, details ------------------------------------------------------------------------

_NO_INJURED = re.compile(
    r"(пострадавших (людей )?нет|без пострадавших|пострадавших не видят|б/п|нет пострадавших|"
    r"информации о пострадавших нет)",
    re.IGNORECASE,
)
_INJURED_COUNT = re.compile(r"(\d+)\s+пострадавш", re.IGNORECASE)
_INJURED_SIGNS = re.compile(
    r"(кровотеч|потеря сознания|без сознания|потеряла сознание|потерял сознание|травм|ударил|"
    r"задыха|отек|отёк|рассек|ожог|плохо (женщине|мужчине|человеку)|выпил|заблокирован|"
    r"кричат о помощи|не открывает дверь)",
    re.IGNORECASE,
)


def injured_summary(situation: str) -> str:
    if _NO_INJURED.search(situation):
        return "нет"
    count = _INJURED_COUNT.search(situation)
    if count:
        return f"есть, {count.group(1)}"
    if _INJURED_SIGNS.search(situation) or re.search(r"пострадавш", situation, re.IGNORECASE):
        return "есть"
    return "неизвестно"


_DETAILS: list[tuple[str, re.Pattern[str]]] = [
    ("floor", re.compile(r"на (\d+)[-‑]?(?:м|ом|ем)? этаже", re.IGNORECASE)),
    ("storeys", re.compile(r"(?:этажность\s*-?\s*(\d+)|в доме (\d+) этаж)", re.IGNORECASE)),
    ("gas", re.compile(r"дом (не )?газифицирован", re.IGNORECASE)),
    ("vehicle", re.compile(r"(иномарка[^,.;]*|ваз[^,.;]*|пежо[^,.;]*|гос\. номер[^,.;]*)", re.I)),
    ("age", re.compile(r"(\d{1,2}) лет", re.IGNORECASE)),
]


def details(situation: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for key, pattern in _DETAILS:
        match = pattern.search(situation)
        if not match:
            continue
        if key == "gas":
            found["gas"] = "не газифицирован" if match.group(1) else "газифицирован"
        elif key == "storeys":
            found["storeys"] = match.group(1) or match.group(2)
        else:
            found[key] = match.group(1).strip()
    return found


# --- address ---------------------------------------------------------------------------------

_REGION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # «в область», «в обл» in tickets is a direction (outbound from МКАД), not a region.
    (re.compile(r"\bМосковская область\b|\bМО\b", re.I), "Московская область"),
    (re.compile(r"\b([А-ЯЁ][а-яё]+ская) обл(?:\.|асть)", re.I), "{} область"),
]
_MOSCOW_TOWNS = re.compile(
    r"(королёв|королев|балашиха|химки|мытищи|подольск|люберцы|одинцов|красногорск|домодедово|"
    r"реутов|дмитровск|раменск|щёлково|щелково|пушкино|ивантеевка|фрязино|жуковский|"
    r"электросталь|ногинск|сергиев посад|клин|коломна|серпухов|чехов|ступино|кашира|"
    r"воскресенск|егорьевск|орехово-зуево|павловский посад|дубна|талдом|волоколамск|"
    r"истра|руза|можайск|наро-фоминск|звенигород|солнечногорск|лобня|долгопрудный)",
    re.IGNORECASE,
)
_STREET_TYPE = re.compile(
    r"\b(ул\.?|улица|пр-т|просп\.?|проспект|пер\.?|переулок|наб\.?|набережная|б-р|бульвар|"
    r"ш\.?|шоссе|пл\.?|площадь|проезд|аллея|туп\.?|тупик|вал|линия|тракт|мост|парк)\b",
    re.IGNORECASE,
)
_HOUSE = re.compile(r"(?:\bдом\.?|\bд\.?|№)\s*(\d+[А-Яа-яA-Za-z]?(?:/\d+)?)", re.IGNORECASE)
_BUILDING = re.compile(r"корп\.?\s*(\d+[А-Яа-я]?)", re.IGNORECASE)
_STRUCTURE = re.compile(r"стр\.?\s*(\d+[А-Яа-я]?)", re.IGNORECASE)
_ENTRANCE = re.compile(r"(?:под\.?|подъезд)\s*(\d+|единственный)", re.IGNORECASE)
_FLOOR = re.compile(r"(?:эт\.?|этаж)\s*(\d+)", re.IGNORECASE)
_CODE = re.compile(r"(?:код|домофон)\s*([\dА-Яа-яA-Za-z]+)", re.IGNORECASE)
_APARTMENT = re.compile(r"кв\.?\s*(\d+)", re.IGNORECASE)
_CITY = re.compile(r"\bг\.\s*([А-ЯЁ][а-яё-]+)|\b(Зеленоград|Балашиха|Волжский|Королёв|Королев)\b")
_DESCRIPTIVE_HINTS = re.compile(
    r"(около|напротив|рядом|между|не доезжая|вход от|со стороны|в сторону|№ дома неизвестен|"
    r"\bкм\b|за деревней|далее по|точка на карте|у «|смотрит|обочина|платформа|съезд|двор\b|"
    r"на пересечении|в область|остановка)",
    re.IGNORECASE,
)
# Prepositions a ticket puts before the street when it describes a place instead of naming an
# address: «на стороне ул. Фабрициуса», «стоят на Ленинградском ш.», «если ехать от…».
_LEADING_PREPOSITION = re.compile(
    r"^(?:стоят на|на стороне|если ехать от|ехали? по|вход от|после|около|напротив|от|до|по)\s+",
    re.IGNORECASE,
)
# A head that names a road or a green area, not a street of the address: the street base has no
# such row, so the trainee would have nothing to pick in the card — it belongs in the description.
_NOT_A_STREET = re.compile(r"^(?:дорога|мкад|съезд|трасса|парк|лесопарк)\b", re.IGNORECASE)
# What is left of «стоят на Ленинградском ш.» after the preposition: an oblique case, i.e. the
# ticket points at a road, it does not name the address. The street base holds nominative names.
_OBLIQUE = re.compile(r"\b[А-Яа-яЁё]+(?:ом|ого|ому|ым|ыми|ых)\b")
_EXACT_IN_BRACKETS = re.compile(r"\(([^()]*(?:дом|д\.|№)[^()]*)\)", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" ,.;")


def _street_from(part: str) -> str | None:
    """The street name of a comma-separated address part, with its type word, without the
    house and everything after it."""
    cut = _HOUSE.search(part)
    head = part[: cut.start()] if cut else part
    hint = _DESCRIPTIVE_HINTS.search(head)
    if hint:
        head = head[: hint.start()]  # «ул. Карла Маркса около комбината» → the street only
    head = _clean(re.sub(r"\(.*", "", head))
    without_preposition = _clean(_LEADING_PREPOSITION.sub("", head))
    if without_preposition != head and _OBLIQUE.search(without_preposition):
        return None
    head = without_preposition
    if not head or not _STREET_TYPE.search(head) or _NOT_A_STREET.search(head):
        return None
    if re.search(r"\d{3,}", head):  # «МЖД Киевская 1 км» is a landmark, not a street
        return None
    return head


def parse_address(text: str) -> Address:
    """Address fields from the ticket's address column. Region stays ``None`` for Moscow."""
    raw = _clean(text)
    address = Address()

    for pattern, template in _REGION_PATTERNS:
        match = pattern.search(raw)
        if match:
            region = template.format(match.group(1)) if "{}" in template else template
            address.region = region[0].upper() + region[1:]
            break
    if address.region is None and _MOSCOW_TOWNS.search(raw) and not raw.startswith("Москва"):
        address.region = "Московская область"

    city = _CITY.search(raw)
    if city:
        address.city = city.group(1) or city.group(2)
        if address.city in {"Зеленоград"}:
            address.city = None  # Zelenograd is a Moscow okrug, not another city

    # An exact address in brackets after a landmark description: parse the bracket.
    exact = _EXACT_IN_BRACKETS.search(raw)
    source = exact.group(1) if exact else raw
    body = re.sub(r"^Москва\s*,?\s*", "", source)

    for key, pattern in (
        ("house", _HOUSE),
        ("building", _BUILDING),
        ("structure", _STRUCTURE),
        ("entrance", _ENTRANCE),
        ("floor", _FLOOR),
        ("code", _CODE),
        ("apartment", _APARTMENT),
    ):
        match = pattern.search(body)
        if match:
            setattr(address, key, match.group(1))

    for part in re.split(r"[,;]", body):
        street = _street_from(part)
        if street:
            address.street = street
            break
    if address.street is None:
        # «Бульвар Маршала Рокоссовского дом 25»: the street is the head before the house.
        head = _street_from(body)
        if head:
            address.street = head

    descriptive = _DESCRIPTIVE_HINTS.search(raw) or address.house is None or address.street is None
    if descriptive:
        address.descriptive = re.sub(r"^Москва\s*,?\s*", "", raw)
    return address


# --- the whole sheet -------------------------------------------------------------------------


@dataclass
class TicketFacts:
    situation: str
    address_text: str
    what_happened: str
    caller: CallerFacts
    address: Address
    injured: str
    details: dict[str, str] = field(default_factory=dict)
    drops_call: bool = False
    child_involved: bool = False
    other_region: bool = False

    def as_dict(self) -> dict:
        data = asdict(self)
        data["address"] = self.address.model_dump(exclude_none=True)
        return data


def _what_happened(situation: str, caller: CallerFacts) -> str:
    text = find_phone(situation)[1]
    if caller.name:
        text = text.replace(caller.name, "")
    text = _CALLED_BY.sub("", text)
    text = re.sub(r"\((очевидец|прохожий|старшая по подъезду)\)", "", text, flags=re.I)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"(\s*,\s*){2,}", ", ", text)
    text = re.sub(r"[,.;\s]*вызывает\s*$", "", text, flags=re.IGNORECASE)
    text = _clean(text)
    return text[0].upper() + text[1:] if text else situation


def parse_ticket(situation: str, address_text: str) -> TicketFacts:
    caller = parse_caller(situation)
    address = parse_address(address_text)
    what = _what_happened(situation, caller)
    return TicketFacts(
        situation=situation,
        address_text=address_text,
        what_happened=what,
        caller=caller,
        address=address,
        injured=injured_summary(situation),
        details=details(situation),
        drops_call=bool(re.search(r"бросил[а]? трубку", situation, re.IGNORECASE)),
        child_involved=bool(re.search(r"(ребен|ребён|подросток)", situation, re.IGNORECASE)),
        other_region=address.region is not None,
    )
