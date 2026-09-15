"""Reference data endpoints: classifier tree, services by type and flags, statuses,
reasons, typical errors, caller topics, tickets, street hints. Available to every
signed-in user; the data comes from the organizers' dataset (wave 1)."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.deps import ActiveUser, DbSession
from app.domain.services import available_flags, resolve_services
from app.errors import ApiError
from app.importers.organizers import normalize_street
from app.models import (
    CallerTopic,
    CardStatus,
    IncidentFlag,
    IncidentGroup,
    IncidentType,
    RejectReason,
    ResponseStatus,
    Service,
    Street,
    Ticket,
    TypicalError,
)

router = APIRouter(tags=["reference"])

STREET_HINT_LIMIT = 20


class ClassifierNode(BaseModel):
    title: str
    type_code: str | None = None
    final_title: str | None = None
    flags: list[str] = []
    children: list["ClassifierNode"] = []


class ClassifierGroupOut(BaseModel):
    code: str
    title: str
    children: list[ClassifierNode]


class ClassifierTreeOut(BaseModel):
    groups: list[ClassifierGroupOut]
    types_total: int


class ServiceOut(BaseModel):
    code: str
    title: str
    short_title: str
    no_reject: bool
    via_arm112: bool


class TypeServicesOut(BaseModel):
    type_code: str
    final_title: str
    main_service: str | None
    flags: list[str]
    available_flags: list[str]
    services: list[ServiceOut]


class IncidentFlagOut(BaseModel):
    code: str
    title: str
    column_hint: str | None


class ResponseStatusOut(BaseModel):
    code: str
    title: str
    order: int
    is_system: bool
    is_primary: bool
    is_final: bool
    requires_comment: bool
    requires_order_number: bool
    allowed_next: list[str]
    description: str | None


class CardStatusOut(BaseModel):
    code: str
    title: str
    is_alert: bool


class RejectReasonOut(BaseModel):
    code: str
    title: str


class CallerTopicOut(BaseModel):
    code: str
    title: str
    keywords: list[str]


class TypicalErrorOut(BaseModel):
    code: str
    title: str
    description: str
    mode: str
    penalty: int
    memo_ref: str | None
    example: str | None


class TicketOut(BaseModel):
    id: str
    ticket_no: int
    item_no: int
    situation: str
    address: str
    ocr_confident: bool
    traps: list[str]


class StreetOut(BaseModel):
    name: str
    okrug: str
    district: str


def _build_tree(types: list[IncidentType]) -> dict[str, list[ClassifierNode]]:
    """Groups types into sign1 → sign2 → sign3 nodes, keeping sheet order.

    A node may be both a type («ДТП» = «ДТП без пострадавших») and a folder for deeper
    signs («ДТП» → «Транспорт легковой»), exactly as the survey card buttons work.
    """
    by_group: dict[str, list[ClassifierNode]] = {}
    for t in types:
        level = by_group.setdefault(t.group_code, [])
        path = [s for s in (t.sign1, t.sign2, t.sign3) if s]
        for i, title in enumerate(path):
            node = next((n for n in level if n.title == title), None)
            if node is None:
                node = ClassifierNode(title=title)
                level.append(node)
            if i == len(path) - 1:
                if node.type_code is not None:
                    # Two rows with identical signs: keep both as separate leaves.
                    node = ClassifierNode(title=title)
                    level.append(node)
                node.type_code = t.code
                node.final_title = t.final_title
                node.flags = list(t.flag_codes)
            level = node.children
    return by_group


@router.get("/classifier/tree", response_model=ClassifierTreeOut)
async def classifier_tree(user: ActiveUser, session: DbSession) -> ClassifierTreeOut:
    groups = (await session.scalars(select(IncidentGroup).order_by(IncidentGroup.number))).all()
    types = (await session.scalars(select(IncidentType).order_by(IncidentType.source_row))).all()
    tree = _build_tree(list(types))
    return ClassifierTreeOut(
        groups=[
            ClassifierGroupOut(code=g.code, title=g.title, children=tree.get(g.code, []))
            for g in groups
        ],
        types_total=len(types),
    )


@router.get("/classifier/{code}/services", response_model=TypeServicesOut)
async def classifier_services(
    code: str,
    user: ActiveUser,
    session: DbSession,
    flags: Annotated[
        str, Query(description="Признаки через запятую, например injured,no_access")
    ] = "",
) -> TypeServicesOut:
    incident_type = await session.get(IncidentType, code)
    if incident_type is None:
        raise ApiError(404, "not_found", f"Тип происшествия {code} не найден.")
    chosen = [f for f in flags.split(",") if f]
    known = set(await session.scalars(select(IncidentFlag.code)))
    unknown = [f for f in chosen if f not in known]
    if unknown:
        raise ApiError(422, "validation_error", f"Неизвестные признаки: {', '.join(unknown)}.")
    codes = resolve_services(incident_type.service_rules, chosen)
    services = {
        s.code: s for s in await session.scalars(select(Service).where(Service.code.in_(codes)))
    }
    return TypeServicesOut(
        type_code=incident_type.code,
        final_title=incident_type.final_title,
        main_service=incident_type.main_service,
        flags=chosen,
        available_flags=available_flags(incident_type.service_rules),
        services=[
            ServiceOut.model_validate(services[c], from_attributes=True)
            for c in codes
            if c in services
        ],
    )


@router.get("/incident-flags", response_model=list[IncidentFlagOut])
async def incident_flags(user: ActiveUser, session: DbSession) -> list[IncidentFlagOut]:
    rows = await session.scalars(select(IncidentFlag).order_by(IncidentFlag.order))
    return [IncidentFlagOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/services", response_model=list[ServiceOut])
async def services(user: ActiveUser, session: DbSession) -> list[ServiceOut]:
    rows = await session.scalars(select(Service).order_by(Service.order))
    return [ServiceOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/response-statuses", response_model=list[ResponseStatusOut])
async def response_statuses(user: ActiveUser, session: DbSession) -> list[ResponseStatusOut]:
    rows = await session.scalars(select(ResponseStatus).order_by(ResponseStatus.order))
    return [ResponseStatusOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/card-statuses", response_model=list[CardStatusOut])
async def card_statuses(user: ActiveUser, session: DbSession) -> list[CardStatusOut]:
    rows = await session.scalars(select(CardStatus).order_by(CardStatus.order))
    return [CardStatusOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/reject-reasons", response_model=list[RejectReasonOut])
async def reject_reasons(user: ActiveUser, session: DbSession) -> list[RejectReasonOut]:
    rows = await session.scalars(select(RejectReason).order_by(RejectReason.order))
    return [RejectReasonOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/caller-topics", response_model=list[CallerTopicOut])
async def caller_topics(user: ActiveUser, session: DbSession) -> list[CallerTopicOut]:
    rows = await session.scalars(select(CallerTopic).order_by(CallerTopic.order))
    return [CallerTopicOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/typical-errors", response_model=list[TypicalErrorOut])
async def typical_errors(
    user: ActiveUser,
    session: DbSession,
    mode: Annotated[str | None, Query(description="card_response или call_intake")] = None,
) -> list[TypicalErrorOut]:
    query = select(TypicalError).order_by(TypicalError.mode, TypicalError.code)
    if mode:
        query = query.where(TypicalError.mode == mode)
    rows = await session.scalars(query)
    return [TypicalErrorOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/tickets", response_model=list[TicketOut])
async def tickets(user: ActiveUser, session: DbSession) -> list[TicketOut]:
    rows = await session.scalars(select(Ticket).order_by(Ticket.ticket_no, Ticket.item_no))
    return [
        TicketOut(
            id=str(r.id),
            ticket_no=r.ticket_no,
            item_no=r.item_no,
            situation=r.situation,
            address=r.address,
            ocr_confident=r.ocr_confident,
            traps=list(r.traps),
        )
        for r in rows
    ]


@router.get("/streets", response_model=list[StreetOut])
async def streets(
    user: ActiveUser,
    session: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=100)],
) -> list[StreetOut]:
    """Prefix search by any word of the street name, «ё» and case insensitive."""
    needle = normalize_street(q)
    query = (
        select(Street)
        .where(
            Street.name_normalized.like(f"{needle}%") | Street.name_normalized.like(f"% {needle}%")
        )
        .order_by(Street.name_normalized, Street.okrug, Street.district)
        .limit(STREET_HINT_LIMIT)
    )
    rows = await session.scalars(query)
    return [StreetOut.model_validate(r, from_attributes=True) for r in rows]
