"""Checks a scenario body against the reference tables and protects approved parts.

``check_body`` returns human-readable problems (Russian, shown to the teacher) instead of
raising: a body with problems can still be saved as a draft, but not approved.
``changed_approved_parts`` lists what an edit tries to change although it was approved:
approved replies and the approved reference never change (PRD 9.6 item 5); a new version with
a revised reference is created through ``revise`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.domain.evaluation.data_check import FIELD_TITLES, values_match
from app.domain.evaluation.schemas import (
    SERVICE_CALL_FACTS,
    CardResponseScenario,
    parse_scenario,
)
from app.domain.reference_data import REJECT_REASONS, RESPONSE_STATUSES
from app.domain.scenarios.generated import TOPIC_CODES
from app.domain.scenarios.officers import default_reports, default_service_calls
from app.domain.scenarios.personas import NOISE_CODES, PERSONA_BY_CODE
from app.domain.services import resolve_services
from app.providers.tts import VOICES

STATUS_CODES = {s["code"] for s in RESPONSE_STATUSES}
REJECT_CODES = {r["code"] for r in REJECT_REASONS}

# Body keys that carry the editorial state; the evaluation engines ignore them.
APPROVED_KEY = "approved_parts"  # {"reference": bool}
GENERATION_KEY = "generation"  # {"method", "model", "phrase", "at"}


@dataclass
class ReferenceCodes:
    """Codes the body may use; loaded from the database by the service layer."""

    incident_types: Mapping[str, Mapping]  # code → row (sign1..3, service_rules, …)
    flags: set[str]
    services: set[str]
    tickets: set[str] = field(default_factory=set)  # «2-1» refs


def check_body(body: Mapping, refs: ReferenceCodes) -> list[str]:
    problems: list[str] = []
    try:
        scenario = parse_scenario(dict(body))
    except (ValidationError, ValueError) as exc:
        return [f"Тело сценария не проходит схему: {str(exc).splitlines()[0]}"]

    if scenario.kind == "call_intake":
        card = scenario.reference_card
        type_code = card.incident_type
        flags = card.flags
        services = list(card.expected_services)
        caller = scenario.caller
        if caller.persona not in PERSONA_BY_CODE:
            problems.append(f"Неизвестный персонаж заявителя: {caller.persona}.")
        if caller.voice and caller.voice not in VOICES:
            problems.append(f"Неизвестный голос: {caller.voice}.")
        if caller.noise and caller.noise not in NOISE_CODES:
            problems.append(f"Неизвестный фон: {caller.noise}.")
        bad_topics = sorted(
            {r.topic for r in scenario.replies if r.topic not in TOPIC_CODES}
            | {t for t in scenario.required_topics if t not in TOPIC_CODES}
        )
        if bad_topics:
            problems.append(f"Неизвестные темы реплик: {', '.join(bad_topics)}.")
        ids = [r.id for r in scenario.replies]
        if len(ids) != len(set(ids)):
            problems.append("Номера реплик повторяются.")
        if not scenario.replies:
            problems.append("У сценария нет ни одной реплики заявителя.")
    else:
        type_code = scenario.card.incident_type
        flags = scenario.card.flags
        services = [n.service for n in scenario.card.notified] + [scenario.service]
        reference = scenario.reference
        if reference.decision == "reject" and reference.reject_reason not in REJECT_CODES:
            problems.append(f"Неизвестная причина отказа: {reference.reject_reason}.")
        bad_statuses = sorted({s.status for s in reference.status_chain} - STATUS_CODES)
        if bad_statuses:
            problems.append(f"Неизвестные статусы в эталоне: {', '.join(bad_statuses)}.")
        problems += _check_injected_errors(scenario, refs)
        for call in reference.service_calls:
            if call.service not in refs.services:
                problems.append(f"Неизвестная служба в звонках: {call.service}.")
            bad_facts = sorted(set(call.required_facts) - set(SERVICE_CALL_FACTS))
            if bad_facts:
                problems.append(f"Неизвестные факты звонка в службу: {', '.join(bad_facts)}.")

    if type_code not in refs.incident_types:
        problems.append(f"Тип происшествия {type_code} отсутствует в классификаторе.")
    bad_flags = sorted(set(flags) - refs.flags)
    if bad_flags:
        problems.append(f"Неизвестные признаки: {', '.join(bad_flags)}.")
    bad_services = sorted(set(services) - refs.services)
    if bad_services:
        problems.append(f"Неизвестные службы: {', '.join(bad_services)}.")
    if scenario.ticket_ref and refs.tickets and scenario.ticket_ref not in refs.tickets:
        problems.append(f"Билет {scenario.ticket_ref} не найден.")
    return problems


def _check_injected_errors(scenario: CardResponseScenario, refs: ReferenceCodes) -> list[str]:
    """Planted operator mistakes (issue #35): known fields, a real difference, codes that
    exist, and the card indeed showing the wrong value."""
    problems: list[str] = []
    seen: set[str] = set()
    for error in scenario.injected_errors:
        if error.field not in FIELD_TITLES:
            problems.append(f"Неизвестное поле заложенной ошибки: {error.field}.")
            continue
        if error.field in seen:
            problems.append(f"Поле {error.field} заложено как ошибка дважды.")
        seen.add(error.field)
        if values_match(error.field, error.correct_value, error.wrong_value, error.correct_label):
            problems.append(
                f"Заложенная ошибка в поле {error.field} не отличается от верного значения."
            )
        if error.field == "incident_type" and error.correct_value not in refs.incident_types:
            problems.append(f"Верный тип {error.correct_value} отсутствует в классификаторе.")
        if error.field == "services":
            code = error.correct_value.lstrip("+-")
            if code not in refs.services:
                problems.append(f"Неизвестная служба в заложенной ошибке: {code}.")
        shown = _card_value(scenario, error.field)
        if shown is not None and not values_match(error.field, error.wrong_value, shown):
            problems.append(
                f"В карточке поле {error.field} = «{shown}», а заложенная ошибка ожидает "
                f"«{error.wrong_value}»."
            )
    return problems


def _card_value(scenario: CardResponseScenario, field: str) -> str | None:
    """What the card shows in the field, in the notation of ``InjectedError`` values."""
    card = scenario.card
    if field.startswith("address."):
        return getattr(card.address, field.split(".", 1)[1], None) or ""
    if field.startswith("flags."):
        return "true" if card.flags.get(field.split(".", 1)[1]) else "false"
    if field.startswith("caller."):
        return getattr(card.caller, field.split(".", 1)[1], None) or ""
    if field == "incident_type":
        return card.incident_type
    if field == "description":
        return card.description
    return None  # services: «+code» / «-code» describe an action, not a value


def fill_from_reference(body: dict, refs: ReferenceCodes) -> dict:
    """Signs and notified services follow the classifier row and the flags, whatever the
    editor typed (PRD 9.3: the services are substituted, so the check is about signs and
    flags). Returns a new body."""
    body = dict(body)
    if body.get("kind") == "call_intake":
        card = dict(body.get("reference_card") or {})
        row = refs.incident_types.get(card.get("incident_type", ""))
        if row is not None:
            card["signs_path"] = [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)]
            chosen = [k for k, v in (card.get("flags") or {}).items() if v]
            card["expected_services"] = resolve_services(row.get("service_rules") or [], chosen)
        body["reference_card"] = card
    else:
        card = dict(body.get("card") or {})
        row = refs.incident_types.get(card.get("incident_type", ""))
        if row is not None:
            card["signs"] = [row[k] for k in ("sign1", "sign2", "sign3") if row.get(k)]
        body["card"] = card
        reference = dict(body.get("reference") or {})
        if "service_calls" not in reference and reference:
            # Issue #36: an accepted card expects a call to the officer of the own service;
            # an explicit empty list in the editor keeps the calls out of the evaluation.
            reference["service_calls"] = default_service_calls({**body, "reference": reference})
            body["reference"] = reference
        if "reports" not in reference and reference:
            # Customer, 21.09.2026: the squad reports by phone; an accepted card gets the
            # default timeline, an explicit empty list turns the reports off.
            reference["reports"] = default_reports({**body, "reference": reference})
            body["reference"] = reference
    return body


def approved_reply_ids(body: Mapping) -> set[int]:
    return {int(r["id"]) for r in body.get("replies") or [] if r.get("approved")}


def reference_approved(body: Mapping) -> bool:
    return bool((body.get(APPROVED_KEY) or {}).get("reference"))


def _reference_part(body: Mapping) -> dict:
    if body.get("kind") == "call_intake":
        return {
            "reference_card": body.get("reference_card"),
            "required_topics": body.get("required_topics"),
        }
    return {
        "card": body.get("card"),
        "reference": body.get("reference"),
        "service": body.get("service"),
        "injected_errors": body.get("injected_errors") or [],
    }


def changed_approved_parts(old: Mapping, new: Mapping) -> list[str]:
    """What the edit changes although it is approved. Empty means the edit is allowed."""
    changed: list[str] = []
    old_replies = {int(r["id"]): r for r in old.get("replies") or []}
    new_replies = {int(r["id"]): r for r in new.get("replies") or []}
    for reply_id in sorted(approved_reply_ids(old)):
        before, after = old_replies[reply_id], new_replies.get(reply_id)
        if after is None:
            changed.append(f"реплика {reply_id} утверждена и не может быть удалена")
            continue
        for key in ("text", "topic", "audio"):
            if before.get(key) != after.get(key):
                changed.append(f"реплика {reply_id} утверждена, поле «{key}» менять нельзя")
                break
    if reference_approved(old) and _reference_part(old) != _reference_part(new):
        changed.append("эталон утверждён; изменить его можно только новой версией («Переделать»)")
    return changed


def approve_all(body: dict) -> dict:
    """The whole scenario approved: every reply and the reference."""
    body = dict(body)
    body["replies"] = [{**r, "approved": True} for r in body.get("replies") or []]
    body[APPROVED_KEY] = {**(body.get(APPROVED_KEY) or {}), "reference": True}
    return body


def is_fully_approved(body: Mapping) -> bool:
    replies = body.get("replies") or []
    replies_ok = (
        all(r.get("approved") for r in replies) if body.get("kind") == "call_intake" else True
    )
    return replies_ok and reference_approved(body)
