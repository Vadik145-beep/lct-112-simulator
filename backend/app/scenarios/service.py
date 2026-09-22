"""Scenario library logic: reference codes, listing, editing with approved-part protection,
generation and revision jobs, approval with grammar check and voicing, preview dialog.

Bodies are stored in ``scenario_versions.body`` (PRD 9.2 / 9.3) plus editorial keys the
engines ignore: ``approved_parts`` ({"reference": bool}), ``generation`` (method, model,
phrase, at), ``traps``. Voiced replies are written to ``STORAGE_DIR/tts/<scenario>/v<N>/r<id>``
— the path ``app.dialog.service.reply_audio`` looks up first, so a training call plays the
file voiced at approval instead of synthesizing.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.audit import write_audit
from app.db import SessionLocal
from app.dialog import service as dialog
from app.domain.evaluation.schemas import CallIntakeScenario, DialogTurn, parse_scenario
from app.domain.reference_data import CALLER_TOPICS
from app.domain.scenarios import validate
from app.domain.scenarios.facts import parse_ticket
from app.domain.scenarios.personas import NOISES, PERSONAS
from app.domain.scenarios.template import ServiceInfo, build_card_from_student
from app.domain.scenarios.validate import ReferenceCodes
from app.errors import ApiError
from app.events import get_redis
from app.logging import get_logger
from app.models import (
    MODE_CALL_INTAKE,
    SCENARIO_APPROVED,
    SCENARIO_ARCHIVED,
    SCENARIO_DRAFT,
    SCENARIO_REVIEW,
    Attempt,
    IncidentFlag,
    IncidentGroup,
    IncidentType,
    Scenario,
    ScenarioVersion,
    Service,
    Ticket,
    User,
)
from app.providers.dialog import DialogContext, get_dialog_provider
from app.providers.generation import (
    GenerationContext,
    GenerationRequest,
    GenerationResult,
    get_generation_provider,
)
from app.providers.grammar import get_grammar_provider
from app.providers.rag import get_reference_index
from app.providers.tts import VOICES, get_tts_provider
from app.scenarios import jobs
from app.scenarios.schemas import (
    GrammarIssueOut,
    GrammarReportOut,
    NoiseOut,
    PersonaOut,
    ReplyOut,
    ScenarioListItem,
    ScenarioOptionsOut,
    ScenarioOut,
    TopicOut,
    VersionOut,
)

log = get_logger(__name__)

TOPIC_TITLES = {t["code"]: t["title"] for t in CALLER_TOPICS}
SOURCES = [
    ("ticket", "Билет (проверен командой)"),
    ("organizers", "Билет (черновик генерации)"),
    ("generated", "Сгенерирован по фразе"),
    ("manual", "Создан вручную"),
    ("student", "Карточка обучающегося"),
]
STATUSES = [
    (SCENARIO_DRAFT, "Черновик"),
    (SCENARIO_REVIEW, "На проверке"),
    (SCENARIO_APPROVED, "Утверждён"),
    (SCENARIO_ARCHIVED, "В архиве"),
]
VOICING_KEY = "scenario-voicing:{id}"
VOICING_TTL_SECONDS = 3600
LIST_LIMIT = 500


# ---------------------------------------------------------------- reference data


async def load_refs(session: AsyncSession) -> ReferenceCodes:
    types = (await session.scalars(select(IncidentType))).all()
    rows = {
        t.code: {
            "code": t.code,
            "group_code": t.group_code,
            "sign1": t.sign1,
            "sign2": t.sign2,
            "sign3": t.sign3,
            "final_title": t.final_title,
            "stat_group": t.stat_group,
            "hints": t.hints,
            "main_service": t.main_service,
            "service_rules": t.service_rules,
            "flag_codes": t.flag_codes,
            "required_topics": t.required_topics,
        }
        for t in types
    }
    flags = set(await session.scalars(select(IncidentFlag.code)))
    services = set(await session.scalars(select(Service.code)))
    tickets = {
        f"{no}-{item}"
        for no, item in await session.execute(select(Ticket.ticket_no, Ticket.item_no))
    }
    return ReferenceCodes(incident_types=rows, flags=flags, services=services, tickets=tickets)


async def load_catalogue(session: AsyncSession) -> dict[str, ServiceInfo]:
    rows = (await session.scalars(select(Service))).all()
    return {s.code: ServiceInfo(s.code, s.via_arm112, s.title) for s in rows}


async def ticket_by_ref(session: AsyncSession, ref: str | None) -> Ticket | None:
    if not ref or "-" not in ref:
        return None
    no, item = ref.split("-", 1)
    if not (no.isdigit() and item.isdigit()):
        return None
    return await session.scalar(
        select(Ticket).where(Ticket.ticket_no == int(no), Ticket.item_no == int(item))
    )


def options(refs_available: dict[str, bool], generation_method: str) -> ScenarioOptionsOut:
    return ScenarioOptionsOut(
        personas=[
            PersonaOut(code=p.code, title=p.title, voice=p.voice, style=p.style) for p in PERSONAS
        ],
        noises=[NoiseOut(code=c, title=t) for c, t in NOISES],
        topics=[TopicOut(code=t["code"], title=t["title"]) for t in CALLER_TOPICS],
        voices=[NoiseOut(code=c, title=v.description) for c, v in VOICES.items()],
        sources=[NoiseOut(code=c, title=t) for c, t in SOURCES],
        statuses=[NoiseOut(code=c, title=t) for c, t in STATUSES],
        generation_method=generation_method,
        generation_available=refs_available["generation"],
        tts_available=refs_available["tts"],
        grammar_available=refs_available["grammar"],
    )


# ---------------------------------------------------------------- loading


@dataclass
class Loaded:
    scenario: Scenario
    version: ScenarioVersion


async def load(
    session: AsyncSession,
    scenario_id: uuid.UUID,
    *,
    for_write: bool = False,
    allow_archived: bool = False,
) -> Loaded:
    query = select(Scenario).where(Scenario.id == scenario_id)
    if for_write:
        query = query.with_for_update()
    scenario = await session.scalar(query)
    if scenario is None:
        raise ApiError(404, "not_found", "Сценарий не найден.")
    if for_write and not allow_archived and scenario.status == SCENARIO_ARCHIVED:
        raise ApiError(409, "scenario_archived", "Сценарий в архиве: сначала восстановите его.")
    version = await session.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == scenario.id,
            ScenarioVersion.version == scenario.current_version,
        )
    )
    if version is None:
        raise ApiError(500, "corrupt_scenario", "У сценария нет текущей версии.")
    return Loaded(scenario, version)


def columns_from_body(body: dict) -> dict[str, Any]:
    """Scenario table columns that mirror the body (list filters read them)."""
    kind = body.get("kind")
    if kind == "call_intake":
        type_code = (body.get("reference_card") or {}).get("incident_type")
        services = (body.get("reference_card") or {}).get("expected_services") or []
        service_code = services[0] if services else None
    else:
        type_code = (body.get("card") or {}).get("incident_type")
        service_code = body.get("service")
    return {
        "kind": kind,
        "title": body.get("title") or "Без названия",
        "ticket_ref": body.get("ticket_ref"),
        "incident_type_code": type_code,
        "service_code": service_code,
        "difficulty": int(body.get("difficulty") or 1),
    }


# ---------------------------------------------------------------- presenting


def _reply_out(reply: dict, voicing: str | None) -> ReplyOut:
    audio = reply.get("audio")
    if audio:
        state = "done"
    elif reply.get("approved") and voicing in {"queued", "running"}:
        state = "queued"
    elif reply.get("approved") and voicing == "failed":
        state = "failed"
    else:
        state = "none"
    return ReplyOut(
        id=int(reply["id"]),
        topic=reply.get("topic", "unknown"),
        topic_title=TOPIC_TITLES.get(reply.get("topic", ""), reply.get("topic", "")),
        text=reply.get("text", ""),
        approved=bool(reply.get("approved")),
        audio_url=f"{dialog_media_prefix()}{audio}" if audio else None,
        source=reply.get("source"),
        voicing=state,  # type: ignore[arg-type]
    )


def dialog_media_prefix() -> str:
    from app.dialog.router import MEDIA_PREFIX

    return MEDIA_PREFIX


async def voicing_state(scenario_id: uuid.UUID) -> str | None:
    job_id = await get_redis().get(VOICING_KEY.format(id=scenario_id))
    if not job_id:
        return None
    job = await jobs.get(job_id)
    return job["status"] if job else None


async def present(session: AsyncSession, loaded: Loaded, refs: ReferenceCodes) -> ScenarioOut:
    scenario, version = loaded.scenario, loaded.version
    body = version.body
    versions = (
        await session.execute(
            select(ScenarioVersion, User.full_name)
            .outerjoin(User, User.id == ScenarioVersion.created_by)
            .where(ScenarioVersion.scenario_id == scenario.id)
            .order_by(ScenarioVersion.version.desc())
        )
    ).all()
    type_code = scenario.incident_type_code or columns_from_body(body)["incident_type_code"]
    type_row = refs.incident_types.get(type_code or "")
    group_title = None
    if type_row:
        group_title = await session.scalar(
            select(IncidentGroup.title).where(IncidentGroup.code == type_row["group_code"])
        )
    ticket = await ticket_by_ref(session, scenario.ticket_ref)
    author = await session.scalar(select(User.full_name).where(User.id == scenario.author_id))
    voicing = await voicing_state(scenario.id) if body.get("kind") == "call_intake" else None
    expected = (
        (body.get("reference_card") or {}).get("expected_services")
        if body.get("kind") == "call_intake"
        else [n.get("service") for n in (body.get("card") or {}).get("notified") or []]
    ) or []
    titles = dict(
        (
            await session.execute(
                select(Service.code, Service.short_title).where(Service.code.in_(expected))
            )
        ).all()
    )
    return ScenarioOut(
        id=scenario.id,
        kind=scenario.kind,  # type: ignore[arg-type]
        title=scenario.title,
        ticket_ref=scenario.ticket_ref,
        ticket_situation=ticket.situation if ticket else None,
        ticket_address=ticket.address if ticket else None,
        incident_type_code=type_code,
        incident_type_title=type_row["final_title"] if type_row else None,
        incident_group_title=group_title,
        service_code=scenario.service_code,
        difficulty=scenario.difficulty,
        status=scenario.status,  # type: ignore[arg-type]
        source=scenario.source,
        current_version=scenario.current_version,
        author=author,
        created_at=scenario.created_at,
        body=body,
        replies=[_reply_out(r, voicing) for r in body.get("replies") or []],
        reference_approved=validate.reference_approved(body),
        fully_approved=validate.is_fully_approved(body),
        problems=validate.check_body(body, refs),
        services=[NoiseOut(code=c, title=titles.get(c, c)) for c in expected],
        versions=[
            VersionOut(
                version=v.version,
                created_at=v.created_at,
                created_by=name,
                revision_comment=v.revision_comment,
                is_current=v.version == scenario.current_version,
            )
            for v, name in versions
        ],
        generation=body.get(validate.GENERATION_KEY),
    )


async def list_scenarios(
    session: AsyncSession,
    *,
    kind: str | None,
    group: str | None,
    source: str | None,
    status: str | None,
    ticket: str | None,
    q: str | None,
) -> list[ScenarioListItem]:
    # Seed scenarios of the call-intake kind carry the type only inside the body.
    type_code = func.coalesce(
        Scenario.incident_type_code,
        ScenarioVersion.body["reference_card"]["incident_type"].astext,
        ScenarioVersion.body["card"]["incident_type"].astext,
    )
    query = (
        select(Scenario, ScenarioVersion, IncidentType, IncidentGroup)
        .join(
            ScenarioVersion,
            (ScenarioVersion.scenario_id == Scenario.id)
            & (ScenarioVersion.version == Scenario.current_version),
        )
        .outerjoin(IncidentType, IncidentType.code == type_code)
        .outerjoin(IncidentGroup, IncidentGroup.code == IncidentType.group_code)
        .order_by(Scenario.created_at.desc())
        .limit(LIST_LIMIT)
    )
    if kind:
        query = query.where(Scenario.kind == kind)
    if group:
        query = query.where(IncidentType.group_code == group)
    if source:
        query = query.where(Scenario.source == source)
    if status:
        query = query.where(Scenario.status == status)
    else:
        # Archived scenarios stay out of the way unless asked for explicitly.
        query = query.where(Scenario.status != SCENARIO_ARCHIVED)
    if ticket:
        query = query.where(Scenario.ticket_ref == ticket)
    if q:
        pattern = f"%{q.strip().lower()}%"
        query = query.where(func.lower(Scenario.title).like(pattern))
    items: list[ScenarioListItem] = []
    for scenario, version, type_row, group_row in (await session.execute(query)).all():
        replies = version.body.get("replies") or []
        items.append(
            ScenarioListItem(
                id=scenario.id,
                kind=scenario.kind,
                title=scenario.title,
                ticket_ref=scenario.ticket_ref,
                incident_type_code=type_row.code if type_row else scenario.incident_type_code,
                incident_type_title=type_row.final_title if type_row else None,
                incident_group_code=group_row.code if group_row else None,
                incident_group_title=group_row.title if group_row else None,
                service_code=scenario.service_code,
                difficulty=scenario.difficulty,
                status=scenario.status,
                source=scenario.source,
                current_version=scenario.current_version,
                replies_total=len(replies),
                replies_approved=sum(1 for r in replies if r.get("approved")),
                replies_pending=sum(
                    1 for r in replies if not r.get("approved") and r.get("source") == "generated"
                ),
                reference_approved=validate.reference_approved(version.body),
                created_at=scenario.created_at,
                updated_at=version.created_at,
            )
        )
    return items


# ---------------------------------------------------------------- creating and editing


async def create(
    session: AsyncSession,
    body: dict,
    *,
    source: str,
    status: str,
    author: User | None,
    refs: ReferenceCodes,
    generation: dict | None = None,
    revision_comment: str | None = None,
) -> Loaded:
    body = validate.fill_from_reference(body, refs)
    if generation:
        body[validate.GENERATION_KEY] = generation
    problems = validate.check_body(body, refs)
    if problems and status == SCENARIO_APPROVED:
        raise ApiError(422, "invalid_scenario", " ".join(problems))
    if any(p.startswith("Тело сценария") for p in problems):
        raise ApiError(422, "invalid_scenario", problems[0])
    ticket = await ticket_by_ref(session, body.get("ticket_ref"))
    scenario = Scenario(
        **columns_from_body(body),
        ticket_id=ticket.id if ticket else None,
        status=status,
        source=source,
        current_version=1,
        author_id=author.id if author else None,
    )
    session.add(scenario)
    await session.flush()
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version=1,
        body=body,
        revision_comment=revision_comment,
        created_by=author.id if author else None,
    )
    session.add(version)
    await session.flush()
    return Loaded(scenario, version)


def _sync_columns(scenario: Scenario, body: dict) -> None:
    for name, value in columns_from_body(body).items():
        setattr(scenario, name, value)


async def update_body(
    session: AsyncSession, loaded: Loaded, new_body: dict, refs: ReferenceCodes, actor: User
) -> Loaded:
    """Edits the current version in place. Approved replies and an approved reference cannot
    change (409): the teacher revises instead."""
    scenario, version = loaded.scenario, loaded.version
    if new_body.get("kind") != version.body.get("kind"):
        raise ApiError(422, "kind_locked", "Режим сценария изменить нельзя.")
    new_body = validate.fill_from_reference(new_body, refs)
    for key in (
        validate.APPROVED_KEY,
        validate.GENERATION_KEY,
        "traps",
        "student_attempt_id",
        "student",
    ):
        if key in version.body and key not in new_body:
            new_body[key] = version.body[key]
    problems = validate.check_body(new_body, refs)
    if any(p.startswith("Тело сценария") for p in problems):
        raise ApiError(422, "invalid_scenario", problems[0])
    locked = validate.changed_approved_parts(version.body, new_body)
    if locked:
        raise ApiError(
            409, "approved_locked", "Нельзя изменить утверждённое: " + "; ".join(locked) + "."
        )
    version.body = new_body
    flag_modified(version, "body")
    _sync_columns(scenario, new_body)
    ticket = await ticket_by_ref(session, new_body.get("ticket_ref"))
    scenario.ticket_id = ticket.id if ticket else None
    if scenario.status == SCENARIO_APPROVED and not validate.is_fully_approved(new_body):
        scenario.status = SCENARIO_REVIEW
    await write_audit(
        session,
        action="scenario.update",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(scenario.id),
        details={"version": version.version},
    )
    return loaded


async def add_version(
    session: AsyncSession, loaded: Loaded, body: dict, *, comment: str | None, author: User | None
) -> Loaded:
    scenario = loaded.scenario
    scenario.current_version += 1
    version = ScenarioVersion(
        scenario_id=scenario.id,
        version=scenario.current_version,
        body=body,
        revision_comment=comment,
        created_by=author.id if author else None,
    )
    session.add(version)
    _sync_columns(scenario, body)
    if scenario.status == SCENARIO_APPROVED and not validate.is_fully_approved(body):
        scenario.status = SCENARIO_REVIEW
    await session.flush()
    return Loaded(scenario, version)


def _replies(body: dict) -> list[dict]:
    return list(body.get("replies") or [])


def _find_reply(body: dict, reply_id: int) -> dict:
    for reply in _replies(body):
        if int(reply.get("id", -1)) == reply_id:
            return reply
    raise ApiError(404, "not_found", f"Реплика {reply_id} не найдена.")


def _require_call_intake(body: dict) -> None:
    if body.get("kind") != "call_intake":
        raise ApiError(422, "no_replies", "Реплики есть только у сценариев приёма вызова.")


async def edit_reply(
    session: AsyncSession,
    loaded: Loaded,
    reply_id: int,
    *,
    text: str | None,
    topic: str | None,
    actor: User,
) -> Loaded:
    version = loaded.version
    _require_call_intake(version.body)
    replies = _replies(version.body)
    reply = _find_reply(version.body, reply_id)
    if reply.get("approved") and (text is not None or topic is not None):
        raise ApiError(
            409, "approved_locked", f"Реплика {reply_id} утверждена: текст и тема не меняются."
        )
    if topic is not None:
        if topic not in TOPIC_TITLES:
            raise ApiError(422, "validation_error", f"Неизвестная тема «{topic}».")
        reply["topic"] = topic
    if text is not None:
        reply["text"] = text.strip()
        reply["audio"] = None  # the recorded voice no longer matches the text
        reply["variants"] = []  # other wordings were written for the old text
    version.body = {**version.body, "replies": replies}
    flag_modified(version, "body")
    await write_audit(
        session,
        action="scenario.reply.update",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"reply_id": reply_id},
    )
    return loaded


async def add_reply(
    session: AsyncSession, loaded: Loaded, *, topic: str, text: str, actor: User
) -> int:
    version = loaded.version
    _require_call_intake(version.body)
    if topic not in TOPIC_TITLES:
        raise ApiError(422, "validation_error", f"Неизвестная тема «{topic}».")
    replies = _replies(version.body)
    next_id = max((int(r.get("id", 0)) for r in replies), default=0) + 1
    replies.append(
        {"id": next_id, "topic": topic, "text": text.strip(), "audio": None, "approved": False}
    )
    version.body = {**version.body, "replies": replies}
    flag_modified(version, "body")
    if loaded.scenario.status == SCENARIO_APPROVED:
        loaded.scenario.status = SCENARIO_REVIEW
    await write_audit(
        session,
        action="scenario.reply.add",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"reply_id": next_id},
    )
    return next_id


async def delete_reply(session: AsyncSession, loaded: Loaded, reply_id: int, actor: User) -> None:
    version = loaded.version
    _require_call_intake(version.body)
    reply = _find_reply(version.body, reply_id)
    if reply.get("approved"):
        raise ApiError(409, "approved_locked", f"Реплика {reply_id} утверждена и не удаляется.")
    version.body = {
        **version.body,
        "replies": [r for r in _replies(version.body) if r is not reply],
    }
    flag_modified(version, "body")
    await write_audit(
        session,
        action="scenario.reply.delete",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"reply_id": reply_id},
    )


# ---------------------------------------------------------------- delete, archive, restore


async def remove(session: AsyncSession, loaded: Loaded, actor: User) -> str:
    """Deletes a scenario nobody has trained on; archives one that attempts refer to.

    Attempts keep ``scenario_id`` (RESTRICT) so reports and reviews of past lessons stay
    readable; an archived scenario is hidden from lists, not offered to new lessons and
    cannot be edited until restored. Returns ``"deleted"`` or ``"archived"``.
    """
    scenario = loaded.scenario
    used = await session.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.scenario_id == scenario.id)
    )
    # A seed scenario is archived even when unused: ``app.seed`` runs at every start and would
    # recreate a deleted one by its seed key; the archive survives the seed (app.seed keeps
    # the status of an archived scenario).
    if used or scenario.seed_key:
        scenario.archived_from = scenario.status
        scenario.status = SCENARIO_ARCHIVED
        result = "archived"
    else:
        await session.delete(scenario)  # versions cascade
        result = "deleted"
    await write_audit(
        session,
        action=f"scenario.{result}",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(scenario.id),
        details={"title": scenario.title, "attempts": int(used or 0)},
    )
    return result


async def restore(session: AsyncSession, loaded: Loaded, actor: User) -> None:
    """Brings an archived scenario back with the status it had before."""
    scenario = loaded.scenario
    if scenario.status != SCENARIO_ARCHIVED:
        raise ApiError(409, "not_archived", "Сценарий не в архиве.")
    scenario.status = scenario.archived_from or SCENARIO_DRAFT
    scenario.archived_from = None
    await write_audit(
        session,
        action="scenario.restore",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(scenario.id),
        details={"status": scenario.status},
    )


# ---------------------------------------------------------------- grammar


def _texts_to_check(
    body: dict, reply_ids: set[int] | None, reference: bool
) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = []
    if reference:
        if body.get("kind") == "call_intake":
            description = (body.get("reference_card") or {}).get("description")
            if description:
                texts.append(("description", description))
        else:
            description = (body.get("card") or {}).get("description")
            if description:
                texts.append(("description", description))
            for index, step in enumerate((body.get("reference") or {}).get("status_chain") or []):
                if step.get("comment_example"):
                    texts.append((f"comment:{index}", step["comment_example"]))
    if reply_ids is not None:
        for reply in _replies(body):
            if int(reply.get("id", -1)) in reply_ids and reply.get("text"):
                texts.append((f"reply:{reply['id']}", reply["text"]))
    return texts


async def grammar_report(
    body: dict, *, reply_ids: set[int] | None, reference: bool
) -> GrammarReportOut:
    """Grammar of the chosen texts; the LanguageTool calls run concurrently (a scenario has
    up to 25 replies and the service is slow when cold)."""
    provider = get_grammar_provider()
    texts = _texts_to_check(body, reply_ids, reference)
    results = await asyncio.gather(*(provider.check(text) for _, text in texts))
    issues: list[GrammarIssueOut] = []
    method = "unavailable"
    available = False
    for (field_name, text), result in zip(texts, results, strict=True):
        method = result.method
        available = available or result.available
        for match in result.matches:
            issues.append(
                GrammarIssueOut(
                    field=field_name,
                    text=text,
                    offset=match.offset,
                    length=match.length,
                    message=match.message,
                    replacements=list(match.replacements)[:5],
                )
            )
    return GrammarReportOut(available=available, method=method, issues=issues)


async def _grammar_gate(
    body: dict, *, reply_ids: set[int] | None, reference: bool, confirm: bool
) -> None:
    if confirm:
        return
    report = await grammar_report(body, reply_ids=reply_ids, reference=reference)
    if report.issues:
        raise ApiError(
            409,
            "grammar_unreviewed",
            f"Проверка грамотности нашла {len(report.issues)} замечаний. Просмотрите их "
            "(«Проверить грамотность») и утвердите повторно с подтверждением.",
        )


# ---------------------------------------------------------------- approval and voicing


async def approve(
    session: AsyncSession,
    loaded: Loaded,
    *,
    reference: bool,
    replies: bool,
    confirm_grammar: bool,
    refs: ReferenceCodes,
    actor: User,
) -> list[int]:
    """Approves the reference and/or all replies; returns the ids of newly approved replies
    (to voice). The scenario becomes ``approved`` when everything is approved."""
    scenario, version = loaded.scenario, loaded.version
    body = dict(version.body)
    problems = validate.check_body(body, refs)
    if problems:
        raise ApiError(422, "invalid_scenario", "Сценарий нельзя утвердить: " + " ".join(problems))
    reply_ids = (
        {int(r["id"]) for r in _replies(body) if not r.get("approved")}
        if replies and body.get("kind") == "call_intake"
        else set()
    )
    await _grammar_gate(
        body, reply_ids=reply_ids or None, reference=reference, confirm=confirm_grammar
    )
    if reference:
        body[validate.APPROVED_KEY] = {**(body.get(validate.APPROVED_KEY) or {}), "reference": True}
    if reply_ids:
        body["replies"] = [
            {**r, "approved": True} if int(r["id"]) in reply_ids else r for r in _replies(body)
        ]
    version.body = body
    flag_modified(version, "body")
    if validate.is_fully_approved(body):
        scenario.status = SCENARIO_APPROVED
    elif scenario.status == SCENARIO_DRAFT:
        scenario.status = SCENARIO_REVIEW
    await write_audit(
        session,
        action="scenario.approve",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(scenario.id),
        details={"reference": reference, "replies": sorted(reply_ids), "version": version.version},
    )
    return sorted(reply_ids)


async def approve_replies(
    session: AsyncSession,
    loaded: Loaded,
    *,
    reply_ids: list[int] | None,
    confirm_grammar: bool,
    actor: User,
) -> list[int]:
    scenario, version = loaded.scenario, loaded.version
    _require_call_intake(version.body)
    body = dict(version.body)
    pending = {int(r["id"]) for r in _replies(body) if not r.get("approved")}
    chosen = pending if reply_ids is None else {i for i in reply_ids if i in pending}
    if reply_ids is not None:
        missing = set(reply_ids) - {int(r["id"]) for r in _replies(body)}
        if missing:
            raise ApiError(404, "not_found", f"Реплики не найдены: {sorted(missing)}.")
    if not chosen:
        return []
    await _grammar_gate(body, reply_ids=chosen, reference=False, confirm=confirm_grammar)
    body["replies"] = [
        {**r, "approved": True} if int(r["id"]) in chosen else r for r in _replies(body)
    ]
    version.body = body
    flag_modified(version, "body")
    if validate.is_fully_approved(body):
        scenario.status = SCENARIO_APPROVED
    elif scenario.status == SCENARIO_DRAFT:
        scenario.status = SCENARIO_REVIEW
    await write_audit(
        session,
        action="scenario.replies.approve",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(scenario.id),
        details={"replies": sorted(chosen)},
    )
    return sorted(chosen)


def audio_stem(scenario_id: uuid.UUID, version: int, reply_id: int) -> Path:
    """Same layout as ``app.dialog.service.reply_audio`` so calls find the file."""
    return Path(dialog.TTS_SUBDIR) / dialog._safe(str(scenario_id)) / f"v{version}" / f"r{reply_id}"


async def schedule_voicing(
    scenario_id: uuid.UUID, version_no: int, reply_ids: list[int], owner: User
) -> str | None:
    """Voices the approved replies in the background (Piper); ``None`` without a voice."""
    if not reply_ids or not dialog.tts_available():
        return None
    job_id = await jobs.create(
        "voicing", owner.id, {"scenario_id": str(scenario_id), "replies": reply_ids}
    )
    await get_redis().set(VOICING_KEY.format(id=scenario_id), job_id, ex=VOICING_TTL_SECONDS)

    async def work(handle: jobs.JobHandle) -> dict:
        voiced = 0
        for index, reply_id in enumerate(reply_ids):
            await handle.progress(
                int(index * 100 / len(reply_ids)), f"Озвучиваем реплику {reply_id}"
            )
            if await voice_reply(scenario_id, version_no, reply_id):
                voiced += 1
        return {"voiced": voiced, "requested": len(reply_ids)}

    jobs.start(job_id, work)
    return job_id


async def voice_reply(scenario_id: uuid.UUID, version_no: int, reply_id: int) -> bool:
    """Synthesizes one reply and stores the path in the body (own transaction)."""
    async with SessionLocal() as session:
        version = await session.scalar(
            select(ScenarioVersion)
            .where(
                ScenarioVersion.scenario_id == scenario_id, ScenarioVersion.version == version_no
            )
            .with_for_update()
        )
        if version is None:
            return False
        body = version.body
        reply = next((r for r in _replies(body) if int(r.get("id", -1)) == reply_id), None)
        if reply is None or not reply.get("text"):
            return False
        if reply.get("audio"):
            return True
        scenario = CallIntakeScenario.model_validate(body)
        stem = audio_stem(scenario_id, version_no, reply_id)
        existing = dialog._existing_audio(stem)
        if existing is None:
            clip = await get_tts_provider().synthesize(
                reply["text"], scenario.caller.voice, scenario.caller.noise, scenario.difficulty
            )
            if clip is None:
                return False
            # WAV + MP3 (ffmpeg) are written off the event loop: a slow encoder must not stall
            # the API while replies are voiced in the background.
            existing = await asyncio.to_thread(dialog._save_audio, clip, stem)
        reply["audio"] = existing
        version.body = {**body, "replies": _replies(body)}
        flag_modified(version, "body")
        await session.commit()
        return True


ALLOWED_AUDIO = {".wav": "audio/wav", ".mp3": "audio/mpeg"}
MAX_AUDIO_BYTES = 10 * 1024 * 1024


async def store_uploaded_audio(
    session: AsyncSession, loaded: Loaded, reply_id: int, filename: str, data: bytes, actor: User
) -> str:
    """The teacher's own recording of a reply replaces the synthesized voice."""
    version = loaded.version
    _require_call_intake(version.body)
    reply = _find_reply(version.body, reply_id)
    suffix = Path(filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO:
        raise ApiError(415, "unsupported_audio", "Загрузите запись в WAV или MP3.")
    if not data:
        raise ApiError(422, "empty_audio", "Пустой файл.")
    if len(data) > MAX_AUDIO_BYTES:
        raise ApiError(413, "audio_too_large", "Запись больше 10 МБ.")
    stem = audio_stem(loaded.scenario.id, version.version, reply_id)
    target = (dialog.storage_root() / stem).with_suffix(suffix)
    target.parent.mkdir(parents=True, exist_ok=True)
    for other in ALLOWED_AUDIO:
        stale = target.with_suffix(other)
        if stale != target and stale.exists():
            stale.unlink()
    target.write_bytes(data)
    reply["audio"] = stem.with_suffix(suffix).as_posix()
    version.body = {**version.body, "replies": _replies(version.body)}
    flag_modified(version, "body")
    await write_audit(
        session,
        action="scenario.reply.audio",
        actor_id=actor.id,
        actor_role=actor.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"reply_id": reply_id},
    )
    return reply["audio"]


# ---------------------------------------------------------------- generation


async def _generation_context(
    session: AsyncSession, refs: ReferenceCodes, query: str
) -> GenerationContext:
    catalogue = await load_catalogue(session)
    index = get_reference_index()
    tickets = (
        await session.execute(
            select(Ticket.ticket_no, Ticket.item_no, Ticket.situation, Ticket.address)
        )
    ).all()
    pairs = [
        (f"{no}-{item}", f"{situation} — {address}") for no, item, situation, address in tickets
    ]

    def retrieve() -> tuple[list, list]:
        # Embedding (e5 on CPU) of the memo and the tickets on first use takes tens of seconds:
        # keep it off the event loop so the API keeps answering while a job runs.
        index.set_tickets(pairs)
        return index.search_memo(query), index.similar_tickets(query)

    memo_chunks, similar = await asyncio.to_thread(retrieve)
    return GenerationContext(
        type_rows=list(refs.incident_types.values()),
        catalogue=catalogue,
        memo_chunks=memo_chunks,
        similar_tickets=similar,
    )


def _generation_meta(
    result: GenerationResult, phrase: str | None, comment: str | None = None
) -> dict:
    return {
        "method": result.method,
        "model": result.model,
        "attempts": result.attempts,
        "latency_ms": result.latency_ms,
        "phrase": phrase,
        "comment": comment,
        "note": result.note,
        "at": datetime.now(UTC).isoformat(),
    }


def generate_from_phrase(job_id: str, request: GenerationRequest, owner_id: uuid.UUID) -> None:
    """Job: a scenario (or both kinds) from the teacher's phrase, stored «на проверке»."""
    kinds = [request.kind] if request.kind else ["call_intake", "card_response"]
    generate_batch(
        job_id,
        [GenerationRequest(**{**request.__dict__, "kind": kind}) for kind in kinds],
        owner_id,
    )


def plan_for_groups(
    refs: ReferenceCodes,
    *,
    kind: str,
    groups: Sequence[str],
    titles: Mapping[str, str],
    difficulty: int,
    service_profile: Sequence[str],
    count: int,
    rng: random.Random | None = None,
) -> list[GenerationRequest]:
    """Requests for a lesson without approved cards (ТЗ, «Настройка учебной среды»: the
    teacher picks the categories, the system generates the scenarios and references).
    ``count`` requests go round-robin over ``groups``; each takes a random incident type of
    its group (within the lesson's services for the response mode), and the phrase names
    that type so both the model and the template land on it."""
    rng = rng or random.Random()  # noqa: S311 — variety, not security
    by_group: dict[str, list[dict]] = {}
    for row in refs.incident_types.values():
        by_group.setdefault(str(row["group_code"]), []).append(row)
    requests: list[GenerationRequest] = []
    for index in range(count):
        group = str(groups[index % len(groups)])
        rows = by_group.get(group, [])
        if kind != "call_intake" and service_profile:
            rows = [r for r in rows if r.get("main_service") in service_profile] or rows
        if not rows:
            continue
        row = rng.choice(rows)
        phrase = f"{row['final_title']} ({titles.get(group, 'группа ' + group)})"
        requests.append(
            GenerationRequest(
                kind=kind,
                phrase=phrase[:500],
                incident_type=str(row["code"]),
                incident_group=group,
                difficulty=difficulty,
            )
        )
    return requests


def generate_batch(job_id: str, requests: Sequence[GenerationRequest], owner_id: uuid.UUID) -> None:
    """Job body shared by «generate by phrase» and «generate for a lesson»: one scenario per
    request, each stored as ``generated``/``review`` for the teacher to approve. A request
    the provider cannot serve fails the whole job (the template never fails), so a partial
    batch is still committed only when every scenario is there."""

    async def work(handle: jobs.JobHandle) -> dict:
        provider = get_generation_provider()
        created: list[dict] = []
        total = max(1, len(requests))
        async with SessionLocal() as session:
            refs = await load_refs(session)
            owner = await session.get(User, owner_id)
            for index, request in enumerate(requests):
                label = "приём вызова" if request.kind == "call_intake" else "реагирование"
                await handle.progress(
                    5 + int(index * 90 / total),
                    f"Генерируем сценарий {index + 1} из {total}: {label}",
                )
                ctx = await _generation_context(session, refs, request.phrase or "")
                result = await provider.generate(request, ctx)
                loaded = await create(
                    session,
                    result.body,
                    source="generated",
                    status=SCENARIO_REVIEW,
                    author=owner,
                    refs=refs,
                    generation=_generation_meta(result, request.phrase),
                )
                await write_audit(
                    session,
                    action="scenario.generate",
                    actor_id=owner_id,
                    actor_role=owner.role if owner else None,
                    entity="scenario",
                    entity_id=str(loaded.scenario.id),
                    details={"method": result.method, "kind": request.kind},
                )
                created.append(
                    {
                        "scenario_id": str(loaded.scenario.id),
                        "kind": request.kind,
                        "title": loaded.scenario.title,
                        "incident_group": request.incident_group,
                        "method": result.method,
                        "note": result.note,
                    }
                )
            await session.commit()
        return {"scenarios": created, "scenario_id": created[0]["scenario_id"] if created else None}

    jobs.start(job_id, work)


def _carry_approved(old: dict, new: dict) -> dict:
    """A revision keeps the approved replies (text, audio, ids) and adds the new ones."""
    approved = [r for r in _replies(old) if r.get("approved")]
    if not approved:
        return new
    approved_texts = {r["text"] for r in approved}
    fresh = [r for r in _replies(new) if r.get("text") not in approved_texts]
    next_id = max((int(r["id"]) for r in approved), default=0) + 1
    renumbered = []
    for reply in fresh:
        renumbered.append({**reply, "id": next_id, "approved": False, "audio": None})
        next_id += 1
    merged = dict(new)
    merged["replies"] = approved + renumbered
    if validate.reference_approved(old):
        merged[validate.APPROVED_KEY] = {"reference": True}
        if old.get("kind") == "call_intake":
            merged["reference_card"] = old.get("reference_card")
            merged["required_topics"] = old.get("required_topics")
        else:
            merged["card"], merged["reference"], merged["service"] = (
                old.get("card"),
                old.get("reference"),
                old.get("service"),
            )
            merged["injected_errors"] = old.get("injected_errors") or []
    return merged


async def revise(job_id: str, scenario_id: uuid.UUID, comment: str, owner_id: uuid.UUID) -> None:
    """Job body: a new version generated with the teacher's comment; approved parts stay."""

    async def work(handle: jobs.JobHandle) -> dict:
        provider = get_generation_provider()
        async with SessionLocal() as session:
            loaded = await load(session, scenario_id, for_write=True)
            refs = await load_refs(session)
            owner = await session.get(User, owner_id)
            old = loaded.version.body
            phrase = (
                ((old.get(validate.GENERATION_KEY) or {}).get("phrase")) or old.get("title") or ""
            )
            ticket = await ticket_by_ref(session, old.get("ticket_ref"))
            await handle.progress(10, "Собираем контекст")
            ctx = await _generation_context(session, refs, f"{phrase}. {comment}")
            request = GenerationRequest(
                kind=old["kind"],
                phrase=phrase,
                comment=comment,
                facts=parse_ticket(ticket.situation, ticket.address) if ticket else None,
                ticket_ref=old.get("ticket_ref"),
                traps=list(old.get("traps") or (ticket.traps if ticket else [])),
                incident_type=(old.get("reference_card") or old.get("card") or {}).get(
                    "incident_type"
                ),
                difficulty=old.get("difficulty"),
                persona=(old.get("caller") or {}).get("persona"),
                noise=(old.get("caller") or {}).get("noise"),
            )
            await handle.progress(30, "Генерируем новую версию")
            result = await provider.generate(request, ctx)
            body = _carry_approved(old, validate.fill_from_reference(result.body, refs))
            body[validate.GENERATION_KEY] = _generation_meta(result, phrase, comment)
            loaded = await add_version(session, loaded, body, comment=comment, author=owner)
            await write_audit(
                session,
                action="scenario.revise",
                actor_id=owner_id,
                actor_role=owner.role if owner else None,
                entity="scenario",
                entity_id=str(scenario_id),
                details={"version": loaded.version.version, "method": result.method},
            )
            await session.commit()
            return {
                "scenario_id": str(scenario_id),
                "version": loaded.version.version,
                "method": result.method,
                "note": result.note,
            }

    jobs.start(job_id, work)


# ---------------------------------------------------------------- preview dialog


@dataclass
class PreviewResult:
    reply: str
    topics: list[str]
    operator_topics: list[str]
    reply_id: int | None
    method: str
    audio: str | None
    latency_ms: int


async def preview(
    loaded: Loaded, text: str, history: list[tuple[str, str]], mode: str | None
) -> PreviewResult:
    """Talk to the caller before approval: every reply counts as approved, nothing is stored."""
    body = loaded.version.body
    _require_call_intake(body)
    scenario = CallIntakeScenario.model_validate(
        {**body, "replies": [{**r, "approved": True} for r in _replies(body)]}
    )
    if not scenario.replies:
        raise ApiError(422, "no_replies", "У сценария нет реплик: нечем отвечать.")
    ctx = DialogContext(
        scenario=scenario,
        history=[DialogTurn(role=role, text=turn_text) for role, turn_text in history],  # type: ignore[arg-type]
        conversation_id=f"preview-{loaded.scenario.id}",
    )
    provider = get_dialog_provider(mode)
    reply = await provider.reply(ctx, text)
    audio = None
    if reply.reply_id is not None:
        original = next((r for r in _replies(body) if int(r["id"]) == reply.reply_id), None)
        audio = (original or {}).get("audio")
    return PreviewResult(
        reply=reply.text,
        topics=reply.topics,
        operator_topics=reply.operator_topics,
        reply_id=reply.reply_id,
        method=reply.method,
        audio=audio,
        latency_ms=reply.latency_ms,
    )


def scenario_body_or_422(body: dict) -> None:
    try:
        parse_scenario(body)
    except Exception as exc:  # pydantic or ValueError: one readable line for the teacher
        raise ApiError(
            422, "invalid_scenario", f"Тело сценария не проходит схему: {str(exc).splitlines()[0]}"
        ) from exc


# ---------------------------------------------------------------- trainee's card → scenario

STUDENT_SOURCE = "student"
STUDENT_ATTEMPT_KEY = "student_attempt_id"


async def scenario_from_attempt(
    session: AsyncSession, attempt: Attempt, *, refs: ReferenceCodes, teacher: User
) -> tuple[Loaded, bool]:
    """The card a trainee saved in call intake becomes a card_response draft
    (``source=student``, PRD 9.6 item 6). Returns (scenario, created); a second call for the
    same attempt returns the existing scenario."""
    if attempt.mode != MODE_CALL_INTAKE:
        raise ApiError(409, "not_call_intake", "Сценарий делается из карточки приёма вызова.")
    if attempt.submitted_at is None or not attempt.draft:
        raise ApiError(409, "card_not_submitted", "Обучающийся ещё не сохранил карточку.")
    existing = await session.scalar(
        select(Scenario)
        .join(
            ScenarioVersion,
            (ScenarioVersion.scenario_id == Scenario.id) & (ScenarioVersion.version == 1),
        )
        .where(ScenarioVersion.body[STUDENT_ATTEMPT_KEY].astext == str(attempt.id))
    )
    if existing is not None:
        return await load(session, existing.id), False

    from app.intake.service import evaluation_card

    card = evaluation_card(attempt.draft)
    type_code = card.get("incident_type") or ""
    row = refs.incident_types.get(type_code)
    if row is None:
        raise ApiError(
            422, "invalid_card", "В карточке не выбран тип происшествия: сценарий не собрать."
        )
    source = await session.get(Scenario, attempt.scenario_id)
    student = await session.get(User, attempt.student_id)
    catalogue = await load_catalogue(session)
    body = build_card_from_student(
        card,
        row,
        title=f"{source.title if source else row['final_title']} — карточка обучающегося",
        ticket_ref=source.ticket_ref if source else None,
        difficulty=source.difficulty if source else 1,
        catalogue=catalogue,
        attempt_id=str(attempt.id),
    )
    body["student"] = {"login": student.login, "full_name": student.full_name} if student else None
    loaded = await create(
        session,
        body,
        source=STUDENT_SOURCE,
        status=SCENARIO_REVIEW,
        author=teacher,
        refs=refs,
        generation={"method": "student", "at": datetime.now(UTC).isoformat()},
        revision_comment="Из карточки обучающегося",
    )
    await write_audit(
        session,
        action="scenario.from_attempt",
        actor_id=teacher.id,
        actor_role=teacher.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"attempt_id": str(attempt.id)},
    )
    return loaded, True
