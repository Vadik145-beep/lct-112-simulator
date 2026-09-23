"""Scenario API (PRD 11, teacher): library, card, generation and revision jobs, approval,
replies, grammar, preview dialog, methodical documents for retrieval, job polling."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from sqlalchemy import select

from app.audit import write_audit
from app.auth.deps import DbSession, require_role
from app.config import get_settings
from app.dialog import service as dialog
from app.dialog.router import MEDIA_PREFIX
from app.domain.scenarios.personas import NOISE_CODES, PERSONA_BY_CODE
from app.errors import ApiError
from app.models import Role, ScenarioVersion, User
from app.providers.generation import GenerationRequest, get_generation_provider
from app.providers.grammar import get_grammar_provider
from app.providers.rag import DOCS_SUBDIR, get_reference_index, split_document
from app.scenarios import jobs
from app.scenarios import service as scenarios
from app.scenarios.schemas import (
    ApproveIn,
    GenerateIn,
    GrammarReportOut,
    JobAcceptedOut,
    JobOut,
    PreviewIn,
    PreviewOut,
    ReferenceDocOut,
    RepliesApproveIn,
    ReplyCreateIn,
    ReplyIn,
    ReviseIn,
    ScenarioCreateIn,
    ScenarioListOut,
    ScenarioOptionsOut,
    ScenarioOut,
    ScenarioRemoveOut,
    ScenarioUpdateIn,
    VersionOut,
)
from app.training import service as training

router = APIRouter(tags=["scenarios"])

# Scenarios belong to teachers only: the administrator neither edits nor approves them (PRD 3).
Teacher = Annotated[User, Depends(require_role(Role.teacher))]

MAX_DOC_BYTES = 20 * 1024 * 1024
DOC_SUFFIXES = {".txt", ".md", ".docx", ".pdf"}


async def _out(session: DbSession, loaded: scenarios.Loaded) -> ScenarioOut:
    refs = await scenarios.load_refs(session)
    return await scenarios.present(session, loaded, refs)


# ---------------------------------------------------------------- options and list


@router.get("/scenarios/options", response_model=ScenarioOptionsOut)
async def scenario_options(user: Teacher) -> ScenarioOptionsOut:
    generation = get_generation_provider()
    grammar = await get_grammar_provider().check("Проверка.")
    return scenarios.options(
        {
            "generation": await generation.available(),
            "tts": dialog.tts_available(),
            "grammar": grammar.available,
        },
        generation.method,
    )


@router.get("/scenarios", response_model=ScenarioListOut)
async def list_scenarios(
    user: Teacher,
    session: DbSession,
    kind: Annotated[str | None, Query(pattern="^(call_intake|card_response)$")] = None,
    group: Annotated[str | None, Query(max_length=8)] = None,
    source: Annotated[str | None, Query(max_length=16)] = None,
    status: Annotated[str | None, Query(pattern="^(draft|review|approved|archived)$")] = None,
    ticket: Annotated[str | None, Query(max_length=16)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> ScenarioListOut:
    items = await scenarios.list_scenarios(
        session, kind=kind, group=group, source=source, status=status, ticket=ticket, q=q
    )
    return ScenarioListOut(items=items, total=len(items))


# ---------------------------------------------------------------- create, read, update


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
async def create_scenario(body: ScenarioCreateIn, user: Teacher, session: DbSession) -> ScenarioOut:
    scenarios.scenario_body_or_422(body.body)
    refs = await scenarios.load_refs(session)
    loaded = await scenarios.create(
        session, body.body, source="manual", status=body.status, author=user, refs=refs
    )
    await write_audit(
        session,
        action="scenario.create",
        actor_id=user.id,
        actor_role=user.role,
        entity="scenario",
        entity_id=str(loaded.scenario.id),
        details={"kind": loaded.scenario.kind},
    )
    await session.commit()
    return await scenarios.present(session, loaded, refs)


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
async def get_scenario(scenario_id: uuid.UUID, user: Teacher, session: DbSession) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id)
    return await _out(session, loaded)


@router.put("/scenarios/{scenario_id}", response_model=ScenarioOut)
async def update_scenario(
    scenario_id: uuid.UUID, body: ScenarioUpdateIn, user: Teacher, session: DbSession
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    refs = await scenarios.load_refs(session)
    loaded = await scenarios.update_body(session, loaded, body.body, refs, user)
    await session.commit()
    return await scenarios.present(session, loaded, refs)


@router.delete("/scenarios/{scenario_id}", response_model=ScenarioRemoveOut)
async def remove_scenario(
    scenario_id: uuid.UUID, user: Teacher, session: DbSession
) -> ScenarioRemoveOut:
    """Deletes a scenario, or archives it when past attempts refer to it (ТЗ: «удалять
    неактуальные сценарии» without losing the history of lessons)."""
    loaded = await scenarios.load(session, scenario_id, for_write=True, allow_archived=True)
    result = await scenarios.remove(session, loaded, user)
    await session.commit()
    return ScenarioRemoveOut(result=result)


@router.post("/scenarios/{scenario_id}/restore", response_model=ScenarioOut)
async def restore_scenario(
    scenario_id: uuid.UUID, user: Teacher, session: DbSession
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True, allow_archived=True)
    await scenarios.restore(session, loaded, user)
    await session.commit()
    return await scenarios.present(session, loaded, await scenarios.load_refs(session))


@router.get("/scenarios/{scenario_id}/versions", response_model=list[VersionOut])
async def scenario_versions(
    scenario_id: uuid.UUID, user: Teacher, session: DbSession
) -> list[VersionOut]:
    out = await get_scenario(scenario_id, user, session)
    return out.versions


@router.get("/scenarios/{scenario_id}/versions/{version}")
async def scenario_version_body(
    scenario_id: uuid.UUID, version: int, user: Teacher, session: DbSession
) -> dict:
    row = await session.scalar(
        select(ScenarioVersion).where(
            ScenarioVersion.scenario_id == scenario_id, ScenarioVersion.version == version
        )
    )
    if row is None:
        raise ApiError(404, "not_found", "Версия не найдена.")
    return {
        "version": row.version,
        "created_at": row.created_at,
        "revision_comment": row.revision_comment,
        "body": row.body,
    }


@router.post("/scenarios/from-attempt/{attempt_id}", response_model=ScenarioOut)
async def scenario_from_attempt(
    attempt_id: uuid.UUID, user: Teacher, session: DbSession, response: Response
) -> ScenarioOut:
    """The trainee's saved card of a call-intake attempt becomes a card_response draft
    (``source=student``); 201 when created, 200 when it already existed."""
    attempt = await training.get_attempt_for(session, attempt_id, user)
    refs = await scenarios.load_refs(session)
    loaded, created = await scenarios.scenario_from_attempt(
        session, attempt, refs=refs, teacher=user
    )
    if created:
        await session.commit()
        response.status_code = 201
    return await scenarios.present(session, loaded, refs)


# ---------------------------------------------------------------- generation jobs


def _check_generation_options(body: GenerateIn) -> None:
    if body.persona and body.persona not in PERSONA_BY_CODE:
        raise ApiError(422, "validation_error", f"Неизвестный персонаж «{body.persona}».")
    if body.noise and body.noise not in NOISE_CODES:
        raise ApiError(422, "validation_error", f"Неизвестный фон «{body.noise}».")


@router.post("/scenarios/generate", response_model=JobAcceptedOut, status_code=202)
async def generate_scenario(body: GenerateIn, user: Teacher, session: DbSession) -> JobAcceptedOut:
    """Starts a generation job; poll ``GET /jobs/{id}``, the result names the scenario."""
    _check_generation_options(body)
    refs = await scenarios.load_refs(session)
    if body.incident_type and body.incident_type not in refs.incident_types:
        raise ApiError(
            422, "validation_error", f"Тип {body.incident_type} не найден в классификаторе."
        )
    request = GenerationRequest(
        kind="" if body.both_kinds else body.kind,
        phrase=body.phrase.strip(),
        incident_type=body.incident_type,
        incident_group=body.incident_group,
        difficulty=body.difficulty,
        persona=body.persona,
        noise=body.noise,
    )
    job_id = await jobs.create("generate", user.id, {"phrase": request.phrase, "kind": body.kind})
    scenarios.generate_from_phrase(job_id, request, user.id)
    return JobAcceptedOut(job_id=job_id)


@router.post("/scenarios/{scenario_id}/revise", response_model=JobAcceptedOut, status_code=202)
async def revise_scenario(
    scenario_id: uuid.UUID, body: ReviseIn, user: Teacher, session: DbSession
) -> JobAcceptedOut:
    loaded = await scenarios.load(session, scenario_id)
    scenarios.guard_delivered(loaded.scenario)
    job_id = await jobs.create("revise", user.id, {"scenario_id": str(scenario_id)})
    await scenarios.revise(job_id, scenario_id, body.comment.strip(), user.id)
    return JobAcceptedOut(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: Teacher) -> JobOut:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ApiError(404, "not_found", "Задача не найдена.")
    job = await jobs.get(job_id)
    if job is None:
        raise ApiError(404, "not_found", "Задача не найдена или устарела.")
    if job.get("owner_id") != str(user.id):
        raise ApiError(403, "forbidden", "Это задача другого пользователя.")
    return JobOut(**{k: job[k] for k in JobOut.model_fields})


# ---------------------------------------------------------------- approval


@router.post("/scenarios/{scenario_id}/approve", response_model=ScenarioOut)
async def approve_scenario(
    scenario_id: uuid.UUID, body: ApproveIn, user: Teacher, session: DbSession
) -> ScenarioOut:
    """Approves the reference and/or every reply. 409 ``grammar_unreviewed`` when the grammar
    check reports issues and ``confirm_grammar`` is false; 422 while the body has problems."""
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    refs = await scenarios.load_refs(session)
    new_replies = await scenarios.approve(
        session,
        loaded,
        reference=body.reference,
        replies=body.replies,
        confirm_grammar=body.confirm_grammar,
        refs=refs,
        actor=user,
    )
    await session.commit()
    await scenarios.schedule_voicing(scenario_id, loaded.version.version, new_replies, user)
    return await scenarios.present(session, loaded, refs)


@router.post("/scenarios/{scenario_id}/grammar", response_model=GrammarReportOut)
async def grammar_scenario(
    scenario_id: uuid.UUID, user: Teacher, session: DbSession
) -> GrammarReportOut:
    """Grammar of the description, reference comments and the not yet approved replies."""
    loaded = await scenarios.load(session, scenario_id)
    body = loaded.version.body
    pending = {int(r["id"]) for r in body.get("replies") or [] if not r.get("approved")}
    return await scenarios.grammar_report(body, reply_ids=pending or None, reference=True)


# ---------------------------------------------------------------- replies


@router.post("/scenarios/{scenario_id}/replies", response_model=ScenarioOut, status_code=201)
async def add_reply(
    scenario_id: uuid.UUID, body: ReplyCreateIn, user: Teacher, session: DbSession
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    await scenarios.add_reply(session, loaded, topic=body.topic, text=body.text, actor=user)
    await session.commit()
    return await _out(session, loaded)


@router.put("/scenarios/{scenario_id}/replies/{reply_id}", response_model=ScenarioOut)
async def edit_reply(
    scenario_id: uuid.UUID, reply_id: int, body: ReplyIn, user: Teacher, session: DbSession
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    await scenarios.edit_reply(
        session, loaded, reply_id, text=body.text, topic=body.topic, actor=user
    )
    await session.commit()
    return await _out(session, loaded)


@router.delete("/scenarios/{scenario_id}/replies/{reply_id}", response_model=ScenarioOut)
async def delete_reply(
    scenario_id: uuid.UUID, reply_id: int, user: Teacher, session: DbSession
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    await scenarios.delete_reply(session, loaded, reply_id, user)
    await session.commit()
    return await _out(session, loaded)


@router.post("/scenarios/{scenario_id}/replies/approve", response_model=ScenarioOut)
async def approve_replies(
    scenario_id: uuid.UUID, body: RepliesApproveIn, user: Teacher, session: DbSession
) -> ScenarioOut:
    """Approves the listed replies (all pending when the list is empty) and voices them."""
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    approved = await scenarios.approve_replies(
        session, loaded, reply_ids=body.reply_ids, confirm_grammar=body.confirm_grammar, actor=user
    )
    await session.commit()
    await scenarios.schedule_voicing(scenario_id, loaded.version.version, approved, user)
    return await _out(session, loaded)


@router.post("/scenarios/{scenario_id}/replies/{reply_id}/audio", response_model=ScenarioOut)
async def upload_reply_audio(
    scenario_id: uuid.UUID,
    reply_id: int,
    user: Teacher,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Запись реплики: WAV или MP3")],
) -> ScenarioOut:
    loaded = await scenarios.load(session, scenario_id, for_write=True)
    data = await file.read()
    await scenarios.store_uploaded_audio(session, loaded, reply_id, file.filename or "", data, user)
    await session.commit()
    return await _out(session, loaded)


# ---------------------------------------------------------------- preview dialog


@router.post("/scenarios/{scenario_id}/preview-dialog", response_model=PreviewOut)
async def preview_dialog(
    scenario_id: uuid.UUID, body: PreviewIn, user: Teacher, session: DbSession
) -> PreviewOut:
    """The caller answers the teacher's phrase as in a call; nothing is stored, all replies
    count as approved so drafts can be tried."""
    loaded = await scenarios.load(session, scenario_id)
    result = await scenarios.preview(
        loaded, body.text, [(t.role, t.text) for t in body.history], body.mode
    )
    return PreviewOut(
        reply=result.reply,
        topics=result.topics,
        operator_topics=result.operator_topics,
        reply_id=result.reply_id,
        method=result.method,
        audio_url=f"{MEDIA_PREFIX}{result.audio}" if result.audio else None,
        latency_ms=result.latency_ms,
    )


# ---------------------------------------------------------------- methodical documents


def _docs_dir() -> Path:
    folder = Path(get_settings().storage_dir) / DOCS_SUBDIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def extract_text(filename: str, data: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        return data.decode("utf-8", errors="replace")
    if suffix == ".docx":
        import io
        import zipfile
        from xml.etree import ElementTree

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml")
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        root = ElementTree.fromstring(xml)  # noqa: S314 - our own upload, parsed once
        paragraphs = []
        for paragraph in root.iterfind(".//w:p", namespace):
            text = "".join(t.text or "" for t in paragraph.iterfind(".//w:t", namespace))
            if text.strip():
                paragraphs.append(text.strip())
        return "\n\n".join(paragraphs)
    if suffix == ".pdf":
        try:
            import io

            from pypdf import PdfReader  # optional: not in the base image
        except ImportError as exc:
            raise ApiError(
                415,
                "pdf_unsupported",
                "Чтение PDF на этом стенде не установлено. Сохраните документ как DOCX или TXT.",
            ) from exc
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    raise ApiError(415, "unsupported_document", "Поддерживаются PDF, DOCX и TXT.")


@router.get("/reference/docs", response_model=list[ReferenceDocOut])
async def list_reference_docs(user: Teacher) -> list[ReferenceDocOut]:
    out = []
    for file in sorted(_docs_dir().glob("*.txt")):
        text = file.read_text(encoding="utf-8")
        out.append(
            ReferenceDocOut(
                name=file.stem,
                chunks=len(split_document(text)),
                size=len(text),
                uploaded_at=datetime.fromtimestamp(file.stat().st_mtime, tz=UTC),
            )
        )
    return out


@router.post("/reference/docs", response_model=ReferenceDocOut, status_code=201)
async def upload_reference_doc(
    user: Teacher,
    session: DbSession,
    file: Annotated[UploadFile, File(description="Методический документ: PDF, DOCX или TXT")],
) -> ReferenceDocOut:
    """Adds a document to the reference used for retrieval during generation."""
    name = Path(file.filename or "document").name
    if Path(name).suffix.lower() not in DOC_SUFFIXES:
        raise ApiError(415, "unsupported_document", "Поддерживаются PDF, DOCX и TXT.")
    data = await file.read()
    if not data:
        raise ApiError(422, "empty_document", "Пустой файл.")
    if len(data) > MAX_DOC_BYTES:
        raise ApiError(413, "document_too_large", "Документ больше 20 МБ.")
    text = extract_text(name, data)
    chunks = split_document(text)
    if not chunks:
        raise ApiError(
            422, "empty_document", "В документе не нашлось текста (скан без слоя текста?)."
        )
    safe_stem = re.sub(r"[^\w\-. ]", "_", Path(name).stem)[:80] or "document"
    target = _docs_dir() / f"{safe_stem}.txt"
    target.write_text(text, encoding="utf-8")
    get_reference_index().invalidate()
    await write_audit(
        session,
        action="reference.doc.upload",
        actor_id=user.id,
        actor_role=user.role,
        entity="reference_doc",
        entity_id=safe_stem,
        details={"chunks": len(chunks), "bytes": len(data)},
    )
    await session.commit()
    return ReferenceDocOut(
        name=safe_stem, chunks=len(chunks), size=len(text), uploaded_at=datetime.now(UTC)
    )


@router.delete("/reference/docs/{name}", status_code=204)
async def delete_reference_doc(name: str, user: Teacher, session: DbSession) -> None:
    target = _docs_dir() / f"{Path(name).name}.txt"
    if not target.exists():
        raise ApiError(404, "not_found", "Документ не найден.")
    target.unlink()
    get_reference_index().invalidate()
    await write_audit(
        session,
        action="reference.doc.delete",
        actor_id=user.id,
        actor_role=user.role,
        entity="reference_doc",
        entity_id=name,
    )
    await session.commit()
