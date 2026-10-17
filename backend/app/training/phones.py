"""The phone directory of the dispatch services (issue #263): the teacher keeps it, the trainee
dials from it.

The teacher's page «Справочник телефонов» lists the numbers of every service and edits them
(one directory for all teachers, up to ten numbers per service, the roles of the customer
only). The trainee sees the book of the service of the card: the people of the own
organization, plus a line «Заявитель — номер из карточки» when the lesson allows calling the
caller (the number itself is not given: the trainee dials what the card shows).
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import write_audit
from app.auth.deps import ActiveUser, DbSession, client_ip, require_role
from app.domain.evaluation.schemas import CardResponseScenario
from app.domain.scenarios import staff
from app.errors import ApiError
from app.models import (
    MAX_PHONE_ENTRIES,
    MODE_CARD_RESPONSE,
    PHONE_NUMBER_DIGITS,
    PhoneEntry,
    Role,
    Service,
    TrainingSession,
    User,
)
from app.training import service as training

router = APIRouter(tags=["phones"])

Teacher = Annotated[User, Depends(require_role(Role.teacher))]

# The first service gets 21xx, the next 22xx… (the customer's example: «2101»).
DEFAULT_NUMBER_BASE = 21
CALLER_ROLE = "caller"
CALLER_TITLE = "Заявитель"
CALLER_NOTE = "номер из карточки"


class PhoneEntryIn(BaseModel):
    service_code: str = Field(min_length=1, max_length=32)
    role: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    number: str = Field(min_length=1, max_length=16)


class PhoneEntryPatch(BaseModel):
    role: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    number: str | None = Field(default=None, min_length=1, max_length=16)


class PhoneEntryOut(BaseModel):
    id: uuid.UUID
    service_code: str
    role: str
    role_title: str
    name: str
    number: str


class PhoneRoleOut(BaseModel):
    code: str
    title: str


class PhoneDirectoryOut(BaseModel):
    entries: list[PhoneEntryOut]
    roles: list[PhoneRoleOut]
    max_per_service: int


class PhoneBookEntryOut(BaseModel):
    role: str
    role_title: str
    name: str
    number: str | None  # none for the caller: the trainee dials what the card shows


class PhoneBookOut(BaseModel):
    service: str
    entries: list[PhoneBookEntryOut]


def digits(number: str | None) -> str:
    return re.sub(r"\D", "", number or "")


def entry_out(entry: PhoneEntry) -> PhoneEntryOut:
    return PhoneEntryOut(
        id=entry.id,
        service_code=entry.service_code,
        role=entry.role,
        role_title=staff.ROLE_TITLES.get(entry.role, entry.role),
        name=entry.name,
        number=entry.number,
    )


def _check_role(role: str) -> None:
    if role not in staff.ROLES:
        titles = ", ".join(f"«{t.lower()}»" for t in staff.ROLE_TITLES.values())
        raise ApiError(422, "unknown_role", f"Должность — одна из четырёх: {titles}.")


def _check_number(number: str) -> str:
    value = number.strip()
    if not re.fullmatch(rf"\d{{{PHONE_NUMBER_DIGITS}}}", value):
        raise ApiError(
            422,
            "bad_number",
            f"Номер — {PHONE_NUMBER_DIGITS} цифры, например 2101. Получено: «{number}».",
        )
    return value


async def _check_free(
    session: AsyncSession, service_code: str, number: str, exclude: uuid.UUID | None = None
) -> None:
    query = select(PhoneEntry.id).where(
        PhoneEntry.service_code == service_code, PhoneEntry.number == number
    )
    if exclude is not None:
        query = query.where(PhoneEntry.id != exclude)
    if await session.scalar(query) is not None:
        raise ApiError(409, "number_taken", f"Номер {number} уже есть в справочнике этой службы.")


async def service_entries(session: AsyncSession, service_code: str) -> list[PhoneEntry]:
    return list(
        await session.scalars(
            select(PhoneEntry)
            .where(PhoneEntry.service_code == service_code)
            .order_by(PhoneEntry.position, PhoneEntry.created_at)
        )
    )


async def find_entry(session: AsyncSession, service_code: str, number: str) -> PhoneEntry | None:
    """The person of the service with the dialled number, if any."""
    return await session.scalar(
        select(PhoneEntry).where(
            PhoneEntry.service_code == service_code, PhoneEntry.number == digits(number)
        )
    )


async def seed_defaults(session: AsyncSession) -> int:
    """The four roles of the customer for every service that has no numbers yet (a clean
    install, a new service); a directory the teacher edited is left as it is."""
    services = list(await session.scalars(select(Service).order_by(Service.order, Service.code)))
    filled = set(await session.scalars(select(PhoneEntry.service_code).distinct()))
    created = 0
    for i, service in enumerate(services):
        if service.code in filled:
            continue
        for k, role in enumerate(staff.ROLES, start=1):
            session.add(
                PhoneEntry(
                    service_code=service.code,
                    role=role,
                    name=staff.DEFAULT_NAMES[role],
                    number=f"{DEFAULT_NUMBER_BASE + i}{k:02d}",
                    position=k,
                )
            )
            created += 1
    await session.flush()
    return created


# ---------------------------------------------------------------- teacher


@router.get("/phone-directory", response_model=PhoneDirectoryOut)
async def list_directory(user: Teacher, session: DbSession) -> PhoneDirectoryOut:
    del user
    rows = await session.scalars(
        select(PhoneEntry)
        .join(Service, Service.code == PhoneEntry.service_code)
        .order_by(Service.order, Service.code, PhoneEntry.position, PhoneEntry.created_at)
    )
    return PhoneDirectoryOut(
        entries=[entry_out(e) for e in rows],
        roles=[PhoneRoleOut(code=c, title=t) for c, t in staff.ROLE_TITLES.items()],
        max_per_service=MAX_PHONE_ENTRIES,
    )


@router.post("/phone-directory", response_model=PhoneEntryOut, status_code=201)
async def add_entry(
    body: PhoneEntryIn, user: Teacher, session: DbSession, request: Request
) -> PhoneEntryOut:
    if await session.get(Service, body.service_code) is None:
        raise ApiError(422, "unknown_service", f"Службы «{body.service_code}» нет в справочнике.")
    _check_role(body.role)
    number = _check_number(body.number)
    count = await session.scalar(
        select(func.count()).where(PhoneEntry.service_code == body.service_code)
    )
    if (count or 0) >= MAX_PHONE_ENTRIES:
        raise ApiError(
            409,
            "directory_full",
            f"У службы уже {MAX_PHONE_ENTRIES} номеров — больше нельзя. "
            "Измените или удалите один из них.",
        )
    await _check_free(session, body.service_code, number)
    position = await session.scalar(
        select(func.coalesce(func.max(PhoneEntry.position), 0)).where(
            PhoneEntry.service_code == body.service_code
        )
    )
    entry = PhoneEntry(
        service_code=body.service_code,
        role=body.role,
        name=body.name.strip(),
        number=number,
        position=(position or 0) + 1,
    )
    session.add(entry)
    await session.flush()
    await _audit(session, user, request, "phone_directory.add", entry)
    await session.commit()
    return entry_out(entry)


@router.patch("/phone-directory/{entry_id}", response_model=PhoneEntryOut)
async def update_entry(
    entry_id: uuid.UUID,
    body: PhoneEntryPatch,
    user: Teacher,
    session: DbSession,
    request: Request,
) -> PhoneEntryOut:
    entry = await _get(session, entry_id)
    if body.role is not None:
        _check_role(body.role)
        entry.role = body.role
    if body.name is not None:
        entry.name = body.name.strip()
    if body.number is not None:
        number = _check_number(body.number)
        await _check_free(session, entry.service_code, number, exclude=entry.id)
        entry.number = number
    await session.flush()
    await _audit(session, user, request, "phone_directory.update", entry)
    await session.commit()
    return entry_out(entry)


@router.delete("/phone-directory/{entry_id}", response_model=PhoneEntryOut)
async def delete_entry(
    entry_id: uuid.UUID, user: Teacher, session: DbSession, request: Request
) -> PhoneEntryOut:
    """Removes the number and returns what was removed."""
    entry = await _get(session, entry_id)
    removed = entry_out(entry)
    await _audit(session, user, request, "phone_directory.delete", entry)
    await session.delete(entry)
    await session.commit()
    return removed


async def _get(session: AsyncSession, entry_id: uuid.UUID) -> PhoneEntry:
    entry = await session.get(PhoneEntry, entry_id)
    if entry is None:
        raise ApiError(404, "phone_entry_not_found", "Такого номера в справочнике нет.")
    return entry


async def _audit(
    session: AsyncSession, user: User, request: Request, action: str, entry: PhoneEntry
) -> None:
    await write_audit(
        session,
        action=action,
        actor_id=user.id,
        actor_role=user.role,
        entity="phone_directory",
        entity_id=str(entry.id),
        details={
            "service": entry.service_code,
            "role": entry.role,
            "name": entry.name,
            "number": entry.number,
        },
        ip=client_ip(request),
    )


# ---------------------------------------------------------------- trainee


@router.get("/attempts/{attempt_id}/phone-book", response_model=PhoneBookOut)
async def phone_book(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> PhoneBookOut:
    """The book of the virtual phone on a card: the people of the card's service and, when
    the lesson allows it, the line of the caller."""
    attempt = await training.get_attempt_for(session, attempt_id, user)
    if attempt.mode != MODE_CARD_RESPONSE:
        raise ApiError(409, "not_card_response", "Телефон есть только в реагировании.")
    ts = await session.get(TrainingSession, attempt.session_id)
    card = await training.load_scenario_card(session, attempt.scenario_id, attempt.scenario_version)
    scenario = CardResponseScenario.model_validate(card.body)
    entries = [
        PhoneBookEntryOut(
            role=e.role,
            role_title=staff.ROLE_TITLES.get(e.role, e.role),
            name=e.name,
            number=e.number,
        )
        for e in await service_entries(session, scenario.service)
    ]
    if ts is not None and ts.allow_caller_call:
        entries.append(
            PhoneBookEntryOut(
                role=CALLER_ROLE, role_title=CALLER_TITLE, name=CALLER_NOTE, number=None
            )
        )
    return PhoneBookOut(service=scenario.service, entries=entries)
