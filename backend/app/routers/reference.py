"""Reference data endpoints: classifier tree, services by type and flags, statuses,
reasons, typical errors, caller topics, tickets, street hints. Available to every
signed-in user; the data comes from the organizers' dataset (wave 1)."""

from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.auth.deps import ActiveUser, DbSession
from app.config import get_settings
from app.domain import materials
from app.domain.memo_search import MIN_QUERY_LENGTH, SEARCH_LIMIT, search_memo
from app.domain.scenarios.classify import type_phrases
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
from app.providers.rag import DOCS_SUBDIR

router = APIRouter(tags=["reference"])

STREET_HINT_LIMIT = 20


class ClassifierNode(BaseModel):
    title: str
    type_code: str | None = None
    final_title: str | None = None
    flags: list[str] = []
    # How a caller names this incident (data/seed/type_synonyms.json): the trainee types
    # «машина упала в воду», the classifier says «Падение автомашины в воду».
    phrases: str | None = None
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


class MemoHitOut(BaseModel):
    page: int
    text: str


class TypeHitOut(BaseModel):
    code: str
    final_title: str
    group_title: str
    signs: list[str]
    main_service: str | None


class DocHitOut(BaseModel):
    name: str
    title: str
    text: str


class ReferenceSearchOut(BaseModel):
    query: str
    memo: list[MemoHitOut]
    types: list[TypeHitOut]
    docs: list[DocHitOut] = []


class MaterialOut(BaseModel):
    name: str
    title: str
    builtin: bool
    paragraphs: int
    size: int
    updated_at: datetime | None


class MaterialParagraphOut(BaseModel):
    page: int | None
    text: str


class MaterialTextOut(BaseModel):
    name: str
    title: str
    builtin: bool
    paragraphs: list[MaterialParagraphOut]


class StreetOut(BaseModel):
    name: str
    okrug: str
    district: str


# Rows of the classifier whose first sign is this marker exist for the services and the
# evaluation (144 types, «Пожар: Автобаза» and the like) but are not offered on the survey card
# of the live АРМ-112, so the tree of the operator's screen leaves them out.
HIDDEN_FROM_OPERATOR = "Не отображается оператору 112"


def _build_tree(types: list[IncidentType]) -> dict[str, list[ClassifierNode]]:
    """Groups types into sign1 → sign2 → sign3 nodes, keeping sheet order.

    A node may be both a type («ДТП» = «ДТП без пострадавших») and a folder for deeper
    signs («ДТП» → «Транспорт легковой»), exactly as the survey card buttons work.
    """
    by_group: dict[str, list[ClassifierNode]] = {}
    for t in types:
        if t.sign1 == HIDDEN_FROM_OPERATOR:
            continue
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
                node.phrases = type_phrases().get(t.code)
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


@router.get("/reference/search", response_model=ReferenceSearchOut)
async def reference_search(
    user: ActiveUser,
    session: DbSession,
    q: Annotated[str, Query(min_length=MIN_QUERY_LENGTH, max_length=100)],
) -> ReferenceSearchOut:
    """Trainee's reference: paragraphs of the memo and classifier types matching the words."""
    memo_path = f"{get_settings().data_dir}/seed/memo.txt"
    words = [w for w in q.lower().replace("ё", "е").split() if len(w) >= MIN_QUERY_LENGTH]
    types: list[TypeHitOut] = []
    if words:
        query = select(IncidentType, IncidentGroup.title).join(
            IncidentGroup, IncidentGroup.code == IncidentType.group_code
        )
        for word in words:
            pattern = f"%{word}%"
            query = query.where(
                func.replace(func.lower(IncidentType.final_title), "ё", "е").like(pattern)
                | func.replace(func.lower(IncidentType.sign1), "ё", "е").like(pattern)
                | func.replace(func.lower(func.coalesce(IncidentType.sign2, "")), "ё", "е").like(
                    pattern
                )
            )
        rows = await session.execute(query.order_by(IncidentType.code).limit(SEARCH_LIMIT))
        types = [
            TypeHitOut(
                code=t.code,
                final_title=t.final_title,
                group_title=group_title,
                signs=[s for s in (t.sign1, t.sign2, t.sign3) if s],
                main_service=t.main_service,
            )
            for t, group_title in rows
        ]
    return ReferenceSearchOut(
        query=q,
        memo=[MemoHitOut(page=h.page, text=h.text) for h in search_memo(memo_path, q)],
        types=types,
        docs=[
            DocHitOut(name=h.name, title=h.title, text=h.text)
            for h in materials.search_uploaded(_docs_dir(), q, SEARCH_LIMIT)
        ],
    )


def _docs_dir() -> Path:
    folder = Path(get_settings().storage_dir) / DOCS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _memo_path() -> str:
    return f"{get_settings().data_dir}/seed/memo.txt"


@router.get("/reference/materials", response_model=list[MaterialOut])
async def list_materials(user: ActiveUser) -> list[MaterialOut]:
    """Methodical materials the trainee can read in full: the memo and the documents a
    teacher uploaded to the reference (ТЗ: «просматривать инструкции и методические
    материалы»)."""
    return [MaterialOut(**m.__dict__) for m in materials.list_materials(_memo_path(), _docs_dir())]


@router.get("/reference/materials/{name}", response_model=MaterialTextOut)
async def read_material(name: str, user: ActiveUser) -> MaterialTextOut:
    found = materials.read_material(name, _memo_path(), _docs_dir())
    if found is None:
        raise ApiError(404, "not_found", "Материал не найден.")
    material, paragraphs = found
    return MaterialTextOut(
        name=material.name,
        title=material.title,
        builtin=material.builtin,
        paragraphs=[MaterialParagraphOut(page=p.page, text=p.text) for p in paragraphs],
    )
