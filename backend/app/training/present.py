"""Turns attempts and scenario bodies (PRD 9.2) into what the journal and the card show."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.evaluation.data_check import field_title
from app.domain.evaluation.status_machine import STATUSES
from app.models import (
    MODE_CALL_INTAKE,
    Attempt,
    CardStatus,
    IncidentGroup,
    IncidentType,
    RejectReason,
    ScenarioVersion,
    Service,
    SessionEvent,
    TrainingSession,
    User,
)
from app.training import service as training
from app.training.schemas import (
    AddressOut,
    ArmInfo,
    AttemptOut,
    CallerOut,
    CardOut,
    FlaggedFieldOut,
    IncidentOut,
    IntakeOut,
    JournalItem,
    RejectReasonOut,
    ServiceCallOut,
    ServiceCallTurnOut,
    ServiceInfo,
    ServiceStatusOut,
    SessionInfo,
    StatusLogEntryOut,
    TransitionOut,
)

# Card flags (PRD 9.2 body) and how the card header names them.
FLAG_INJURED = "injured"
FLAG_AMBULANCE_REFUSED = "ambulance_refused"
FLAG_BLOCKED = "blocked"
FLAG_EMERGENCY = "emergency"  # ЧС
FLAG_INCIDENT = "incident"  # ЧП

# Operator and workstation numbers shown when a scenario does not set its own.
DEFAULT_OPERATOR_NO = "227"
DEFAULT_ARM_NO = "007"


@dataclass
class Lookups:
    services: dict[str, Service]
    card_statuses: dict[str, CardStatus]
    reject_reasons: dict[str, RejectReason]
    incident_types: dict[str, IncidentType] = field(default_factory=dict)
    incident_groups: dict[str, IncidentGroup] = field(default_factory=dict)


async def load_lookups(session: AsyncSession, type_codes: set[str]) -> Lookups:
    codes = {c for c in type_codes if c}
    types: dict[str, IncidentType] = {}
    groups: dict[str, IncidentGroup] = {}
    if codes:
        types = {
            t.code: t
            for t in await session.scalars(select(IncidentType).where(IncidentType.code.in_(codes)))
        }
        group_codes = {t.group_code for t in types.values()}
        groups = {
            g.code: g
            for g in await session.scalars(
                select(IncidentGroup).where(IncidentGroup.code.in_(group_codes))
            )
        }
    return Lookups(
        services=await training.load_services(session),
        card_statuses={c.code: c for c in await session.scalars(select(CardStatus))},
        reject_reasons=await training.load_reject_reasons(session),
        incident_types=types,
        incident_groups=groups,
    )


async def load_versions(
    session: AsyncSession, attempts: list[Attempt]
) -> dict[tuple[uuid.UUID, int], ScenarioVersion]:
    if not attempts:
        return {}
    ids = {a.scenario_id for a in attempts}
    rows = await session.scalars(
        select(ScenarioVersion).where(ScenarioVersion.scenario_id.in_(ids))
    )
    return {(v.scenario_id, v.version): v for v in rows}


async def last_seq(session: AsyncSession, session_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.max(SessionEvent.seq)).where(SessionEvent.session_id == session_id)
    )
    return value or 0


# ---------------------------------------------------------------- formatting


def format_address(addr: dict) -> str:
    """One line like the card header: «Россия, Москва, (СЗАО, Хорошёво-Мнёвники), улица
    Берзарина, 21, к. 1, под. 3»."""
    if not addr:
        return ""
    if addr.get("descriptive") and not addr.get("street"):
        return str(addr["descriptive"])
    parts = ["Россия"]
    if addr.get("region"):
        parts.append(str(addr["region"]))
    if addr.get("city") or not addr.get("region"):
        parts.append(addr.get("city") or "Москва")
    area = ", ".join(p for p in (addr.get("okrug"), addr.get("district")) if p)
    if area:
        parts.append(f"({area})")
    if addr.get("street"):
        parts.append(str(addr["street"]))
    if addr.get("house"):
        parts.append(str(addr["house"]))
    for key, label in (
        ("building", "к."),
        ("structure", "стр."),
        ("entrance", "под."),
        ("floor", "эт."),
        ("apartment", "кв."),
    ):
        if addr.get(key):
            parts.append(f"{label} {addr[key]}")
    text = ", ".join(parts)
    if addr.get("descriptive"):
        text += f" ({addr['descriptive']})"
    return text


def _address_out(addr: dict) -> AddressOut:
    fields = {
        k: str(addr.get(k) or "")
        for k in (
            "street",
            "house",
            "building",
            "structure",
            "entrance",
            "floor",
            "apartment",
            "code",
            "okrug",
            "district",
            "descriptive",
        )
    }
    return AddressOut(text=format_address(addr), **fields)


def _caller_out(caller: dict) -> CallerOut:
    return CallerOut(
        name=str(caller.get("name") or ""),
        role=str(caller.get("role") or ""),
        phone=str(caller.get("phone") or ""),
    )


def _status_title(code: str, lookups: Lookups) -> str:
    del lookups  # titles come from the memo constants, kept for symmetry with the others
    return training.status_title(code)


def _incident_out(body: dict, lookups: Lookups) -> IncidentOut:
    card = body.get("card", {})
    code = card.get("incident_type")
    itype = lookups.incident_types.get(code or "")
    group = lookups.incident_groups.get(itype.group_code) if itype else None
    signs = [str(s) for s in card.get("signs") or []]
    if not signs and itype:
        signs = [s for s in (itype.sign1, itype.sign2, itype.sign3) if s]
    return IncidentOut(
        type_code=code,
        group_title=group.title if group else "",
        final_title=itype.final_title if itype else str(card.get("incident_title") or ""),
        signs=signs,
    )


# The classifier notifies «Территориальные ОИВ» as one abstract service; on the live АРМ-112 the
# tab names the district's own ДДС («Упр. района Вешняки», «Поселение Вороновское») and the
# prefecture of the okrug — the customer's answer of 21.09.2026 to question 4. The names are
# derived from the card's address; the service code and the evaluation do not change.
TERRITORIAL_CODES = frozenset({"territorial_oiv", "territorial_oiv_tinao"})


def territorial_titles(address: dict, fallback_title: str, fallback_short: str) -> tuple[str, str]:
    district = str(address.get("district") or "").strip()
    okrug = str(address.get("okrug") or "").strip()
    if not district:
        return fallback_title, fallback_short
    lowered = district.lower()
    if lowered.startswith("район "):
        short = "Упр. " + district
    elif lowered.endswith(" район"):
        short = "Упр. " + district
    else:
        short = "Упр. района " + district
    title = f"ДДС управы: {district}"
    if okrug:
        title += f", префектура {okrug}"
    return title, short


def _service_statuses(attempt: Attempt, body: dict, lookups: Lookups) -> list[ServiceStatusOut]:
    """Tabs of the «Службы» strip: the trainee's service from the attempt log, the rest from
    the scenario."""
    card = body.get("card", {})
    own_code = body.get("service") or ""
    itype = lookups.incident_types.get(str(card.get("incident_type") or ""))
    main_code = itype.main_service if itype else None
    address = card.get("address") or {}
    result: list[ServiceStatusOut] = []
    seen: set[str] = set()

    def add(code: str, status: str, at: datetime | None, is_own: bool) -> None:
        if code in seen:
            return
        seen.add(code)
        service = lookups.services.get(code)
        title = service.title if service else code
        short_title = service.short_title if service else code
        if code in TERRITORIAL_CODES:
            title, short_title = territorial_titles(address, title, short_title)
        result.append(
            ServiceStatusOut(
                code=code,
                title=title,
                short_title=short_title,
                status=status,
                status_title=_status_title(status, lookups),
                at=at,
                is_own=is_own,
                is_main=code == main_code,
            )
        )

    last = attempt.status_log[-1] if attempt.status_log else None
    last_at = datetime.fromisoformat(last["at"]) if last else attempt.issued_at
    add(own_code, attempt.response_status, last_at, True)
    for item in card.get("notified") or []:
        code = str(item.get("service") or "")
        if not code:
            continue
        status = str(item.get("status") or training.STATUS_ADDED)
        # Scenario statuses may be titles («Получена службой») or codes; accept both.
        for status_code, spec in STATUSES.items():
            if spec["title"] == status:
                status = status_code
                break
        add(code, status, attempt.issued_at, False)
    return result


def _service_info(code: str | None, lookups: Lookups) -> ServiceInfo | None:
    service = lookups.services.get(code or "")
    if service is None:
        return None
    return ServiceInfo(
        code=service.code,
        title=service.title,
        short_title=service.short_title,
        no_reject=service.no_reject,
    )


def session_info(ts: TrainingSession, lookups: Lookups, service_code: str | None) -> SessionInfo:
    return SessionInfo(
        id=ts.id,
        title=ts.title,
        mode=ts.mode,
        status=ts.status,
        difficulty=ts.difficulty,
        norm_seconds=ts.norm_seconds,
        hints_enabled=ts.hints_enabled,
        dialog_mode=ts.dialog_mode,
        started_at=ts.started_at,
        finished_at=ts.finished_at,
        service=_service_info(service_code, lookups),
    )


def arm_info(student: User) -> ArmInfo:
    # Dispatcher numbers are not part of the dataset: a stable pseudo-number from the login
    # keeps the header realistic and distinct per trainee.
    digits = "".join(ch for ch in student.login if ch.isdigit()) or "1"
    return ArmInfo(
        dispatcher=student.full_name,
        operator_no=str(int(digits) % 1000).rjust(3, "0"),
        arm_no=str(100 + int(digits) % 900),
    )


def journal_item(
    attempt: Attempt, body: dict, ts: TrainingSession, lookups: Lookups
) -> JournalItem:
    card = body.get("card", {})
    flags = card.get("flags") or {}
    incident = _incident_out(body, lookups)
    card_status = lookups.card_statuses.get(attempt.card_status)
    return JournalItem(
        attempt_id=attempt.id,
        card_number=attempt.card_number,
        state=attempt.state,
        response_status=attempt.response_status,
        response_status_title=_status_title(attempt.response_status, lookups),
        card_status=attempt.card_status,
        card_status_title=card_status.title if card_status else attempt.card_status,
        card_status_alert=bool(card_status and card_status.is_alert),
        issued_at=attempt.issued_at,
        received_at=attempt.received_at,
        primary_status_at=attempt.primary_status_at,
        submitted_at=attempt.submitted_at,
        norm_seconds=ts.norm_seconds,
        incident_title=incident.final_title,
        incident_group=incident.group_title,
        injured=bool(flags.get(FLAG_INJURED)),
        address=format_address(card.get("address") or {}),
        caller=_caller_out(card.get("caller") or {}),
        description=str(card.get("description") or ""),
        signs=incident.signs,
        operator_no=str(card.get("operator_no") or DEFAULT_OPERATOR_NO),
        arm_no=str(card.get("arm_no") or DEFAULT_ARM_NO),
        services=_service_statuses(attempt, body, lookups),
    )


def card_out(attempt: Attempt, body: dict, lookups: Lookups) -> CardOut:
    card = body.get("card", {})
    flags = {k: bool(v) for k, v in (card.get("flags") or {}).items()}
    caller = card.get("caller") or {}
    phone = str(caller.get("phone") or "")
    return CardOut(
        number=attempt.card_number,
        created_at=attempt.issued_at,
        operator_no=str(card.get("operator_no") or DEFAULT_OPERATOR_NO),
        arm_no=str(card.get("arm_no") or DEFAULT_ARM_NO),
        caller=_caller_out(caller),
        address=_address_out(card.get("address") or {}),
        description=str(card.get("description") or ""),
        incident=_incident_out(body, lookups),
        flags=flags,
        injured=flags.get(FLAG_INJURED, False),
        injured_count=card.get("injured_count"),
        ambulance_refused=flags.get(FLAG_AMBULANCE_REFUSED, False),
        blocked=flags.get(FLAG_BLOCKED, False),
        emergency=flags.get(FLAG_EMERGENCY, False),
        incident_flag=flags.get(FLAG_INCIDENT, False),
        phones={
            "aon": str(card.get("aon") or phone),
            "provided": str(card.get("provided_phone") or phone),
            "on_site": str(card.get("on_site_phone") or ""),
        },
        services=_service_statuses(attempt, body, lookups),
    )


def status_log_out(attempt: Attempt, lookups: Lookups) -> list[StatusLogEntryOut]:
    result = []
    for entry in attempt.status_log:
        reason = lookups.reject_reasons.get(entry.get("reject_reason") or "")
        result.append(
            StatusLogEntryOut(
                status=entry["status"],
                title=_status_title(entry["status"], lookups),
                order_number=entry.get("order_number"),
                comment=entry.get("comment"),
                reject_reason=entry.get("reject_reason"),
                reject_reason_title=reason.title if reason else None,
                at=datetime.fromisoformat(entry["at"]),
                by=entry.get("by") or training.BY_DISPATCHER,
            )
        )
    return result


def service_call_out(attempt: Attempt, body: dict, call: dict, lookups: Lookups) -> ServiceCallOut:
    service = lookups.services.get(call.get("service") or "")
    # Facts are passed on the dispatcher's own calls only; a report has nothing to pass.
    required = next(
        (
            list(c.get("required_facts") or [])
            for c in (body.get("reference") or {}).get("service_calls") or []
            if c.get("service") == call.get("service") and call.get("kind") != "report"
        ),
        [],
    )
    started = datetime.fromisoformat(call["started_at"])
    ended = datetime.fromisoformat(call["ended_at"]) if call.get("ended_at") else None
    turns = call.get("dialog") or []
    report_status = call.get("report_status")
    return ServiceCallOut(
        id=call["id"],
        service=call["service"],
        service_title=service.title if service else call.get("service_title") or call["service"],
        kind=call.get("kind") or "outgoing",
        report_status=report_status,
        report_status_title=_status_title(report_status, lookups) if report_status else None,
        started_at=started,
        answered=bool(call.get("answered")),
        answered_at=datetime.fromisoformat(call["answered_at"])
        if call.get("answered_at")
        else None,
        ended_at=ended,
        end_reason=call.get("end_reason"),
        telephony=bool(call.get("telephony")),
        seconds=round((ended - started).total_seconds(), 1) if ended else None,
        facts_passed=list(call.get("facts_passed") or []),
        facts_required=required,
        recording_available=bool(call.get("recording_path")),
        turns=[
            ServiceCallTurnOut(
                index=i,
                role=t["role"],
                text=t["text"],
                topics=list(t.get("topics") or []),
                at=datetime.fromisoformat(t["at"]) if t.get("at") else None,
                audio_url=f"/api/media/{t['audio']}" if t.get("audio") else None,
                heard=bool(t.get("heard")),
                generated=bool(t.get("generated")),
            )
            for i, t in enumerate(turns)
        ],
    )


def service_calls_out(attempt: Attempt, body: dict, lookups: Lookups) -> list[ServiceCallOut]:
    return [service_call_out(attempt, body, c, lookups) for c in attempt.service_calls or []]


def caller_phone(attempt: Attempt, body: dict) -> str:
    """АОН of the call: the scenario's phone when it has one, otherwise a stable number
    derived from the attempt so the panel never shows an empty АОН."""
    phone = ((body.get("reference_card") or {}).get("caller") or {}).get("phone")
    if phone:
        return str(phone)
    digits = f"{attempt.id.int % 10_000_000:07d}"
    tail = attempt.card_number[-2:].rjust(2, "0")
    return f"+7 (9{digits[:2]}) {digits[2:5]}-{digits[5:7]}-{tail}"


def intake_out(attempt: Attempt, body: dict, finished: bool) -> IntakeOut | None:
    if attempt.mode != MODE_CALL_INTAKE:
        return None
    return IntakeOut(
        caller_phone=caller_phone(attempt, body),
        draft=attempt.draft,
        title=str(body.get("title") or "") if finished else None,
        required_topics=[str(t) for t in body.get("required_topics") or []],
    )


def reference_out(attempt: Attempt, body: dict, finished: bool) -> dict | None:
    """The reference solution, visible once the card is closed (PRD 11): the status chain
    of a card, the reference card of a call."""
    if not finished:
        return None
    if attempt.mode == MODE_CALL_INTAKE:
        return body.get("reference_card")
    return body.get("reference")


def attempt_out(
    attempt: Attempt,
    body: dict,
    ts: TrainingSession,
    student: User,
    lookups: Lookups,
    *,
    seq: int,
    evaluation: dict | None = None,
) -> AttemptOut:
    own_code = body.get("service") or None
    service = lookups.services.get(own_code or "")
    card_status = lookups.card_statuses.get(attempt.card_status)
    finished = attempt.state in training.CLOSED_STATES
    transitions = training.transitions_for(attempt, service)
    return AttemptOut(
        id=attempt.id,
        session=session_info(ts, lookups, own_code),
        arm=arm_info(student),
        state=attempt.state,
        response_status=attempt.response_status,
        response_status_title=_status_title(attempt.response_status, lookups),
        card_status=attempt.card_status,
        card_status_title=card_status.title if card_status else attempt.card_status,
        card_status_alert=bool(card_status and card_status.is_alert),
        issued_at=attempt.issued_at,
        received_at=attempt.received_at,
        primary_status_at=attempt.primary_status_at,
        submitted_at=attempt.submitted_at,
        norm_seconds=ts.norm_seconds,
        card=card_out(attempt, body, lookups),
        service=_service_info(own_code, lookups),
        status_log=status_log_out(attempt, lookups),
        flagged_fields=[
            FlaggedFieldOut(
                field=f["field"],
                title=field_title(f["field"]),
                corrected_value=f["corrected_value"],
                at=datetime.fromisoformat(f["at"]),
            )
            for f in attempt.flagged_fields or []
        ],
        service_calls=service_calls_out(attempt, body, lookups),
        service_calls_required=[
            c.get("service") for c in (body.get("reference") or {}).get("service_calls") or []
        ],
        reports_expected=bool((body.get("reference") or {}).get("reports")),
        transitions=[TransitionOut(**t.__dict__) for t in transitions],
        reject_reasons=[
            RejectReasonOut(code=r.code, title=r.title)
            for r in sorted(lookups.reject_reasons.values(), key=lambda r: r.order)
        ],
        reference=reference_out(attempt, body, finished),
        evaluation=evaluation,
        intake=intake_out(attempt, body, finished),
        last_seq=seq,
    )
