"""Template generation: scenario bodies of both modes from a fact sheet and a classifier row,
without a model (PRD 9.6 item 7). The drafts land in ``review`` and the teacher edits them;
the model-based generator (``app.providers.generation``) produces richer text but uses the
same reference-card logic from here.

Pure: no database. The caller passes the classifier row, the resolved services and the
service catalogue.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.evaluation.schemas import Address
from app.domain.scenarios.facts import TicketFacts
from app.domain.scenarios.personas import DEFAULT_NOISE, persona
from app.domain.services import resolve_services

DEFAULT_CALL_NORM_SECONDS = 90
DROPPED_CALL_NORM_SECONDS = 60
CARD_NORM_SECONDS = 30
# The card_response scenario is written for a city dispatch service; when the situation
# notifies none, the district authority receives the card and rejects it (PRD 8.3).
FALLBACK_CARD_SERVICE = "territorial_oiv"
CARD_NUMBER_DIGITS = 8

# Trap code (tickets.traps, importers.organizers.TRAP_PATTERNS) → what the caller does.
TRAP_BEHAVIOUR: dict[str, str] = {
    "descriptive_address": "адрес называет ориентирами; точный дом или строение говорит "
    "только после уточняющего вопроса",
    "other_region": "не говорит сам, что это не Москва; город и регион называет, только "
    "если спросить",
    "call_dropped": "раздражён, после второго вопроса бросает трубку",
    "injured": "о пострадавших говорит сам, подробности (сколько, в сознании ли) — по вопросу",
    "child": "звонит не пострадавший; о ребёнке говорит с тревогой",
    "caller_not_victim": "заявитель не пострадавший, отвечает то, что видит",
    "entrance_details": "подъезд, этаж и код домофона называет только по вопросу",
    "no_ambulance": "сам говорит, что скорая не нужна",
}


@dataclass(frozen=True)
class ServiceInfo:
    code: str
    via_arm112: bool


# --- helpers ---------------------------------------------------------------------------------


def _lower_first(text: str) -> str:
    """«Горит балкон» → «горит балкон», but «ДТП …» stays."""
    if len(text) > 1 and text[1].islower():
        return text[0].lower() + text[1:]
    return text


def _short_title(text: str, limit: int = 70) -> str:
    first = re.split(r"[.;]| — ", text, maxsplit=1)[0].strip(" ,")
    first = re.sub(r",\s*(пострадавших нет|без пострадавших)$", "", first, flags=re.IGNORECASE)
    if len(first) > limit:
        first = first[:limit].rsplit(" ", 1)[0] + "…"
    return first[0].upper() + first[1:] if first else text[:limit]


def _address_sentence(address: Address) -> str:
    parts: list[str] = []
    if address.region:
        parts.append(address.region)
    if address.city:
        parts.append(f"город {address.city}")
    if address.street:
        parts.append(address.street)
    if address.house:
        parts.append(f"дом {address.house}")
    if address.building:
        parts.append(f"корпус {address.building}")
    if address.structure:
        parts.append(f"строение {address.structure}")
    if address.entrance:
        parts.append(f"подъезд {address.entrance}")
    if address.floor:
        parts.append(f"этаж {address.floor}")
    if address.apartment:
        parts.append(f"квартира {address.apartment}")
    if address.code:
        parts.append(f"код домофона {address.code}")
    if not parts and address.descriptive:
        return address.descriptive
    return ", ".join(parts)


def _spoken_house(address: Address) -> str:
    bits: list[str] = []
    if address.street:
        bits.append(address.street.replace("ул. ", "улица ").replace("пл ", "площадь "))
    if address.house:
        bits.append(f"дом {address.house}")
    if address.building:
        bits.append(f"корпус {address.building}")
    if address.structure:
        bits.append(f"строение {address.structure}")
    return ", ".join(bits)


def _keywords(text: str, limit: int = 4) -> list[str]:
    from app.domain.scenarios.classify import STOP

    words = [
        w
        for w in re.findall(r"[а-яёА-ЯЁ][а-яё-]{3,}", text)
        if w.lower() not in STOP and not w[0].isupper()
    ]
    seen: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered not in seen:
            seen.append(lowered)
    return seen[:limit]


def _flags(facts: TicketFacts, row: Mapping) -> dict[str, bool]:
    text = facts.situation.lower()
    allowed = set(row.get("flag_codes") or [])
    # «Пострадавшие» is asked on every card; the other flags only where the row allows them.
    flags: dict[str, bool] = {"injured": facts.injured.startswith("есть")}
    if "no_access" in allowed:
        flags["no_access"] = bool(re.search(r"не открывает|заблокирован|закрыт", text))
    if "threat" in allowed:
        flags["threat"] = bool(re.search(r"угроза|кричат о помощи|тикает|оруж|нож", text))
    if "mass_incident" in allowed:
        flags["mass_incident"] = bool(re.search(r"\b(\d{2,})\s*(-\s*\d+\s*)?человек", text))
    if "medical_help" in allowed:
        flags["medical_help"] = facts.injured.startswith("есть")
    if "gasification" in allowed and facts.details.get("gas"):
        flags["gasification"] = facts.details["gas"] == "газифицирован"
    return flags


def _persona_code(facts: TicketFacts) -> str:
    if facts.drops_call:
        return "angry_customer"
    if facts.caller.relation in {"ребёнок", "ребенок", "подросток"}:
        return "child"
    if facts.caller.relation in {"пожилой человек", "пожилая женщина", "пенсионер", "пенсионерка"}:
        return "elderly_calm"
    if facts.caller.relation in {"мама", "мать", "папа", "отец"} and facts.child_involved:
        return "mother_anxious"
    if facts.caller.relation in {"супруг", "супруга", "муж", "жена", "брат", "сестра"}:
        return "worried_resident"
    if re.search(r"пожилой|инвалид|бабушк|дедушк", facts.situation, re.IGNORECASE):
        return "elderly_calm"
    if facts.injured.startswith("есть"):
        return "witness_shaken"
    digest = hashlib.sha1(facts.situation.encode()).digest()[0]  # noqa: S324 - not security
    return ("calm", "worried_resident", "witness_shaken")[digest % 3]


def _noise(facts: TicketFacts, indoors: bool) -> str:
    text = f"{facts.situation} {facts.address_text}".lower()
    if re.search(r"дерутся|драка|вокзал|метро|толп|беспорядк", text):
        return "crowd"
    if indoors or re.search(r"квартир|подъезд|в доме|салон|магазин", text):
        return DEFAULT_NOISE
    return "street"


def _difficulty(traps: list[str], facts: TicketFacts) -> int:
    hard = {"descriptive_address", "other_region", "call_dropped"}
    level = 1 + len(hard & set(traps))
    if facts.injured.startswith("есть") and level < 3:
        level += 0 if level == 2 else 1
    return max(1, min(level, 3))


# --- call_intake -----------------------------------------------------------------------------


def _facts_sheet(facts: TicketFacts) -> dict[str, str]:
    sheet: dict[str, str] = {"what_happened": facts.what_happened}
    exact = _address_sentence(facts.address)
    if facts.address.descriptive and (not exact or not facts.address.house):
        sheet["address"] = facts.address.descriptive
    else:
        sheet["address"] = exact or facts.address_text
    if facts.address.region:
        city = f", город {facts.address.city}" if facts.address.city else ""
        sheet["region"] = f"{facts.address.region}{city}, не Москва"
    sheet["injured"] = facts.injured
    if facts.caller.name:
        who = f", {facts.caller.relation}" if facts.caller.relation else ""
        sheet["caller_name"] = f"{facts.caller.name}{who}"
    elif facts.caller.relation:
        sheet["caller_name"] = f"имени не называет, {facts.caller.relation} пострадавшего"
    if facts.caller.phone:
        sheet["callback_phone"] = facts.caller.phone
    for key, label in (
        ("floor", "этаж заявителя"),
        ("storeys", "этажность дома"),
        ("gas", "газификация"),
        ("vehicle", "транспорт"),
        ("age", "возраст"),
    ):
        if key in facts.details:
            sheet[key] = (
                f"{label}: {facts.details[key]}" if key != "gas" else f"дом {facts.details[key]}"
            )
    return sheet


def _opening(facts: TicketFacts, persona_code: str) -> str:
    what = _lower_first(facts.what_happened)
    what = re.split(r"[.;]", what, maxsplit=1)[0].strip(" ,")
    lead = {
        "angry_customer": "Значит так, ",
        "mother_anxious": "Помогите, пожалуйста! У нас ",
        "elderly_calm": "Алло, дежурный? Тут ",
        "elderly_panicked": "Алло! Алло! Тут ",
        "witness_shaken": "Алло... тут ",
        "worried_resident": "Здравствуйте, у нас ",
        "child": "Алло, тётя, у нас ",
        "drunk": "Слушайте, тут ",
    }.get(persona_code, "Здравствуйте, ")
    tail = " Приезжайте скорее!" if persona_code not in {"calm", "angry_customer"} else ""
    return f"{lead}{what}.{tail}".replace("  ", " ")


def _replies(facts: TicketFacts, persona_code: str, flags: dict[str, bool]) -> list[dict]:
    p = persona(persona_code)

    def filler_join(text: str) -> str:
        """«Ой, » + «Да, есть…» → «Ой, да, есть…»."""
        if not p.interjection or not text:
            return text
        return p.interjection + _lower_first(text)

    exact = _spoken_house(facts.address)
    descriptive = facts.address.descriptive
    replies: list[tuple[str, str]] = []

    what = _lower_first(facts.what_happened)
    replies.append(("what_happened", filler_join(f"{what}.")))
    replies.append(
        ("what_happened", f"Я же говорю: {re.split(r'[.;,]', what)[0]}. Что ещё вам сказать?")
    )

    if descriptive and exact:
        replies.append(("address", f"Это {descriptive}."))
        replies.append(("address", f"Точный адрес... {exact}."))
    elif descriptive:
        replies.append(("address", f"Это {descriptive}."))
        replies.append(("address", "Точнее не скажу, дома тут нет, ориентируйтесь по месту."))
    else:
        replies.append(
            ("address", filler_join(f"{exact}.") if exact else "Адрес… сейчас… не помню точно.")
        )
        replies.append(("address", f"{exact}, записали?" if exact else "Ну здесь, рядом со мной!"))

    entrance_bits = []
    if facts.address.entrance:
        entrance_bits.append(f"подъезд {facts.address.entrance}")
    if facts.address.floor or facts.details.get("floor"):
        entrance_bits.append(f"этаж {facts.address.floor or facts.details.get('floor')}")
    if facts.address.apartment:
        entrance_bits.append(f"квартира {facts.address.apartment}")
    if facts.address.code:
        entrance_bits.append(f"код домофона {facts.address.code}")
    if entrance_bits:
        replies.append(("entrance_floor_code", f"{', '.join(entrance_bits).capitalize()}."))
    else:
        replies.append(("entrance_floor_code", "Это на улице, никакого подъезда."))

    if facts.address.region:
        city = f", город {facts.address.city}" if facts.address.city else ""
        replies.append(("region", f"{facts.address.region}{city}. А что, разница есть?"))
    else:
        replies.append(("region", "Москва, конечно, какой ещё регион."))

    if facts.injured == "нет":
        replies.append(("injured", "Нет, пострадавших нет, слава богу."))
        replies.append(("injured", "Никто не пострадал, я же сказал."))
    elif facts.injured.startswith("есть"):
        count = facts.injured.split(", ")[1] if ", " in facts.injured else None
        detail = f"пострадавших {count}" if count else "есть пострадавший"
        replies.append(("injured", filler_join(f"Да, {detail}!")))
        replies.append(("injured", "Не знаю, насколько тяжело, но помощь нужна."))
    else:
        replies.append(("injured", "Не знаю, не видно отсюда."))
        replies.append(("injured", "Пострадавших не видел, но может кто и есть."))

    if flags.get("threat") or re.search(r"горит|пламя|дым|газ", facts.situation, re.IGNORECASE):
        replies.append(("danger", "Пока не распространяется, но страшно, вдруг перекинется!"))
    else:
        replies.append(("danger", "Никакой другой угрозы не вижу."))

    replies.append(("count_people", "Я один тут звоню… ну, люди вокруг есть."))
    if facts.details.get("vehicle"):
        replies.append(("vehicle", f"Машина — {facts.details['vehicle']}."))
    elif re.search(r"дтп|машин|автомоб", facts.situation, re.IGNORECASE):
        replies.append(("vehicle", "Номер не запомнил, обычная легковая."))
    replies.append(("time", "Только что, минут пять назад."))

    if facts.caller.name:
        replies.append(("caller_name", f"{facts.caller.name}."))
    else:
        replies.append(("caller_name", "Да какая разница, как меня зовут, приезжайте!"))
    if facts.caller.phone:
        replies.append(("callback_phone", f"Телефон {facts.caller.phone}, с него и звоню."))
    else:
        replies.append(("callback_phone", "С этого номера и звоню, он определился же."))

    role_text = {
        "родственник": f"Я {facts.caller.relation or 'родственник'}.",
        "участник": "Я сам это всё и есть, участник, так сказать.",
        "пострадавший": "Это я пострадал.",
    }.get(facts.caller.role, "Я просто мимо проходил, увидел и звоню.")
    replies.append(("caller_role", role_text))

    replies.append(("repeat", "Что? Не расслышал, повторите!"))
    replies.append(("unknown", "Не знаю я, приезжайте скорее!"))
    if facts.drops_call:
        replies.append(("unknown", "Да ну вас, сам разберусь. [бросает трубку]"))
    else:
        replies.append(("unknown", "Это вам виднее, я не специалист."))

    return [
        {"id": index, "topic": topic, "text": text, "audio": None, "approved": False}
        for index, (topic, text) in enumerate(replies, start=1)
    ]


def _required_topics(facts: TicketFacts, row: Mapping, indoors: bool) -> list[str]:
    topics = ["what_happened", "address", "injured", "caller_name", "callback_phone"]
    if facts.address.region:
        topics.insert(2, "region")
    if indoors and any([facts.address.entrance, facts.address.floor, facts.address.code]):
        topics.insert(2, "entrance_floor_code")
    if str(row.get("group_code")) == "1" or re.search(r"газ", facts.situation, re.IGNORECASE):
        topics.append("danger")
    if str(row.get("group_code")) == "2" or facts.details.get("vehicle"):
        topics.append("vehicle")
    if re.search(r"дерутся|драка|\d+\s*человек", facts.situation, re.IGNORECASE):
        topics.append("count_people")
    for extra in row.get("required_topics") or []:
        if extra not in topics:
            topics.append(extra)
    return topics


def build_call_intake(
    facts: TicketFacts,
    row: Mapping,
    *,
    ticket_ref: str | None,
    traps: list[str],
    persona_code: str | None = None,
    noise: str | None = None,
    difficulty: int | None = None,
) -> dict:
    """PRD 9.3 body. ``row`` is the classifier type (``IncidentType`` columns as a mapping)."""
    indoors = any([facts.address.entrance, facts.address.floor, facts.address.apartment])
    persona_code = persona_code or _persona_code(facts)
    flags = _flags(facts, row)
    signs_path = [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)]
    reference_address = facts.address.model_copy()
    behaviour = (
        "; ".join(TRAP_BEHAVIOUR[t] for t in traps if t in TRAP_BEHAVIOUR) or "отвечает по делу"
    )
    return {
        "kind": "call_intake",
        "title": _short_title(facts.what_happened),
        "ticket_ref": ticket_ref,
        "difficulty": difficulty or _difficulty(traps, facts),
        "norm_seconds": DROPPED_CALL_NORM_SECONDS
        if facts.drops_call
        else DEFAULT_CALL_NORM_SECONDS,
        "caller": {
            "persona": persona_code,
            "voice": persona(persona_code).voice,
            "noise": noise or _noise(facts, indoors),
            "opening": _opening(facts, persona_code),
            "facts": _facts_sheet(facts),
            "behaviour": behaviour,
            "drops_call": facts.drops_call,
            "no_contact": False,
        },
        "replies": _replies(facts, persona_code, flags),
        "required_topics": _required_topics(facts, row, indoors),
        "reference_card": {
            "signs_path": signs_path,
            "incident_type": row["code"],
            "flags": flags,
            "address": reference_address.model_dump(exclude_none=True),
            "caller": {
                "name": facts.caller.name,
                "role": facts.caller.relation or facts.caller.role,
                "phone": facts.caller.phone,
            },
            "description_keywords": _keywords(facts.what_happened),
            "description": facts.what_happened.rstrip(".") + ".",
            "expected_services": resolve_services(
                row.get("service_rules") or [], [k for k, v in flags.items() if v]
            ),
        },
        "traps": traps,
    }


# --- card_response ---------------------------------------------------------------------------


def _card_number(seed: str) -> str:
    digest = hashlib.sha1(seed.encode()).hexdigest()  # noqa: S324 - a stable fake number
    return str(int(digest[:12], 16))[-CARD_NUMBER_DIGITS:].rjust(CARD_NUMBER_DIGITS, "3")


def choose_card_service(
    expected: list[str], catalogue: Mapping[str, ServiceInfo], main: str | None
) -> str:
    """The dispatch service the card_response scenario is written for: the first notified
    service that works on АРМ-112; otherwise the district authority."""
    for code in expected:
        info = catalogue.get(code)
        if info and info.via_arm112:
            return code
    if main and catalogue.get(main) and catalogue[main].via_arm112:
        return main
    return FALLBACK_CARD_SERVICE


def build_card_response(
    facts: TicketFacts,
    row: Mapping,
    *,
    ticket_ref: str | None,
    traps: list[str],
    catalogue: Mapping[str, ServiceInfo],
    difficulty: int | None = None,
) -> dict:
    """PRD 9.2 body: the same situation as a filled card for a city dispatch service."""
    flags = _flags(facts, row)
    expected = resolve_services(row.get("service_rules") or [], [k for k, v in flags.items() if v])
    service = choose_card_service(expected, catalogue, row.get("main_service"))
    signs = [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)]

    reference = card_reference(region=facts.address.region, service=service, expected=expected)

    notified = [{"service": s, "status": "Получена службой"} for s in expected if s != service]
    notified.append({"service": service, "status": "Добавлена"})

    description = facts.what_happened.rstrip(".") + "."
    if facts.caller.relation and facts.caller.relation not in description.lower():
        description += f" Вызывает {facts.caller.relation}."

    return {
        "kind": "card_response",
        "title": _short_title(facts.what_happened),
        "ticket_ref": ticket_ref,
        "service": service,
        "difficulty": difficulty or _difficulty(traps, facts),
        "norm_seconds": CARD_NORM_SECONDS,
        "card": {
            "number": _card_number(f"{ticket_ref}:{facts.situation}"),
            "incident_type": row["code"],
            "signs": signs,
            "flags": flags,
            "address": facts.address.model_dump(exclude_none=True),
            "caller": {
                "name": facts.caller.name,
                "role": facts.caller.relation or facts.caller.role,
                "phone": facts.caller.phone,
            },
            "description": description,
            "notified": notified,
        },
        "reference": reference,
        "traps": traps,
    }


def card_reference(*, region: str | None, service: str, expected: list[str]) -> dict:
    """The reference decision of a card_response scenario: reject for another region or a
    service the situation does not notify, otherwise the full accept chain (PRD 8.3, 9.2)."""
    if region:
        decision, reason = "reject", "not_our_territory"
        comment = (
            f"Адрес вне Москвы ({region}), территория не обслуживается; "
            "информация передана оператору 112"
        )
    elif service not in expected:
        decision, reason = "reject", "not_in_competence"
        comment = (
            "Происшествие не относится к компетенции службы, реагирование ведут оповещённые "
            "экстренные службы"
        )
    else:
        decision, reason, comment = "accept", None, None

    if decision == "accept":
        chain = [
            {"status": "accepted"},
            {
                "status": "response_started",
                "order_number": True,
                "comment_example": "Направлен дежурный наряд",
            },
            {"status": "arrived"},
            {"status": "works_started", "comment_example": "Проводятся работы на месте"},
            {"status": "works_done", "comment_example": "Работы завершены, пострадавших нет"},
        ]
        critical = ["late_primary", "progress_missing"]
    else:
        chain = [{"status": "rejected", "comment_example": comment}]
        critical = [
            "empty_reject_comment",
            "competence_refusal" if reason == "not_in_competence" else "late_primary",
        ]
    return {
        "decision": decision,
        "reject_reason": reason,
        "status_chain": chain,
        "critical_errors": critical,
    }


def build_card_from_student(
    card: Mapping,
    row: Mapping,
    *,
    title: str,
    ticket_ref: str | None,
    difficulty: int,
    catalogue: Mapping[str, ServiceInfo],
    attempt_id: str,
) -> dict:
    """PRD 9.6 item 6: the card a trainee saved in call intake (``attempts.draft`` as the
    engine's ``SubmittedCard``) becomes a card_response scenario for a dispatch service. The
    services the trainee listed are what the card notifies; the reference decision follows the
    classifier row, as for generated cards."""
    flags = {k: bool(v) for k, v in (card.get("flags") or {}).items()}
    expected = resolve_services(row.get("service_rules") or [], [k for k, v in flags.items() if v])
    listed = [s for s in card.get("services") or [] if s]
    service = choose_card_service(listed or expected, catalogue, row.get("main_service"))
    address = {k: v for k, v in (card.get("address") or {}).items() if v}
    caller = {k: (v or None) for k, v in (card.get("caller") or {}).items()}
    notified_codes = [s for s in dict.fromkeys(listed + expected) if s != service]
    notified = [{"service": s, "status": "Получена службой"} for s in notified_codes]
    notified.append({"service": service, "status": "Добавлена"})
    description = (card.get("description") or "").strip() or row.get("final_title", "")
    return {
        "kind": "card_response",
        "title": title,
        "ticket_ref": ticket_ref,
        "service": service,
        "difficulty": max(1, min(int(difficulty or 1), 3)),
        "norm_seconds": CARD_NORM_SECONDS,
        "card": {
            "number": _card_number(f"student:{attempt_id}"),
            "incident_type": row["code"],
            "signs": [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)],
            "flags": flags,
            "address": address,
            "caller": caller,
            "description": description,
            "notified": notified,
        },
        "reference": card_reference(
            # «Москва» typed by the trainee is not another region.
            region=None if Address(region=address.get("region")).is_moscow() else address["region"],
            service=service,
            expected=expected,
        ),
        "student_attempt_id": attempt_id,
    }
