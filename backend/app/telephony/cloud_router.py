"""The cloud voice over HTTP (plan/track-c-vapi.md).

* The webhook of Vapi: every server message of a cloud call lands here, SIP or browser.
  No user session: the request proves itself with the ``X-Trainer-Secret`` header the
  backend put into the number's and the assistants' server configuration.
* The browser call of a call-intake attempt (no telephony): the trainee's panel asks for the
  keys to start it with the Vapi Web SDK, and reports when the SDK could not.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.auth.deps import ActiveUser, DbSession
from app.config import get_settings
from app.dialog import call as call_state
from app.dialog import officer
from app.dialog import service as dialog
from app.dialog.router import _turn_out
from app.dialog.schemas import DialogTurnOut
from app.errors import ApiError
from app.events import publish_events
from app.telephony import cloud_web, service_calls
from app.telephony import service as telephony
from app.telephony.calls import LoadedAttempt, LoadedCardAttempt
from app.telephony.cloud import CloudCallManager
from app.telephony.vapi import SECRET_HEADER, WEBHOOK_PATH, VapiError, webhook_secret
from app.training import service as training

router = APIRouter(tags=["cloud-voice"])
API_PREFIX = "/api"
ROUTE_PATH = WEBHOOK_PATH.removeprefix(API_PREFIX)


# ---------------------------------------------------------------- webhook


@router.post(ROUTE_PATH, include_in_schema=False)
async def vapi_webhook(request: Request) -> dict:
    if not get_settings().cloud_voice_enabled:
        raise ApiError(404, "not_found", "Облачный голос выключен.")
    given = request.headers.get(SECRET_HEADER, "")
    if not secrets.compare_digest(given, webhook_secret()):
        raise ApiError(401, "unauthorized", "Неверный секрет вебхука.")
    try:
        body = await request.json()
    except ValueError as exc:
        raise ApiError(400, "bad_json", "Тело запроса не JSON.") from exc
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        raise ApiError(400, "bad_message", "В запросе нет сообщения.")
    service = telephony.get_service()
    manager = service.calls if service is not None else None
    sip_manager = manager if isinstance(manager, CloudCallManager) else None
    web_calls = cloud_web.get_calls()
    # assistant-request comes for SIP calls only (the browser starts with an assistant id).
    if message.get("type") == "assistant-request":
        if sip_manager is None:
            return {"error": "Телефония стенда выключена: учебный вызов не найден."}
        return await sip_manager.webhook(message)
    if sip_manager is not None and sip_manager.call_for_message(message) is not None:
        return await sip_manager.webhook(message)
    if web_calls is not None and web_calls.call_for_message(message) is not None:
        return await web_calls.webhook(message)
    if sip_manager is not None:
        return await sip_manager.webhook(message)
    return {}


# ---------------------------------------------------------------- browser calls


class WebCallOut(BaseModel):
    """What the browser starts the call with (Vapi Web SDK)."""

    public_key: str
    api_url: str
    assistant_id: str
    token: str


class WebCallFailedIn(BaseModel):
    reason: str = ""


@router.post("/attempts/{attempt_id}/cloud-call", response_model=WebCallOut)
async def start_web_call(attempt_id: uuid.UUID, user: ActiveUser, session: DbSession) -> WebCallOut:
    """Keys of the browser call for the attempt: the assistant of its scenario is stored in
    Vapi and the caller answers in the trainee's browser. 409 when the lesson is not in the
    cloud mode or the calls go through telephony; 503 when the cloud is unreachable (the
    panel then continues with the microphone and the stand-by provider)."""
    attempt, ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    if not dialog.cloud_lesson(ts):
        raise ApiError(409, "not_cloud_lesson", "В этом занятии заявителя играет локальная модель.")
    if telephony.for_session(ts):
        raise ApiError(409, "telephony_active", "Звонок идёт через телефонию стенда.")
    web_calls = cloud_web.get_calls()
    if web_calls is None:
        raise ApiError(503, "cloud_unavailable", "Облачный голос не запущен.")
    # The state «в разговоре» first (idempotent): the opening is what Vapi says first.
    _, events = await call_state.answer(session, attempt, ts, version, scenario, telephony=False)
    await session.commit()
    await publish_events(events)
    try:
        start = await web_calls.start(LoadedAttempt(attempt, ts, version, scenario))
    except VapiError as exc:
        await web_calls.failed(attempt.id, f"start: {exc}")
        raise ApiError(
            503,
            "cloud_unavailable",
            f"Облачный голос недоступен: {exc}. Разговор продолжится локально.",
        ) from exc
    return WebCallOut(
        public_key=start.public_key,
        api_url=start.api_url,
        assistant_id=start.assistant_id,
        token=start.token,
    )


@router.post("/attempts/{attempt_id}/service-call/{call_id}/cloud-call", response_model=WebCallOut)
async def start_service_web_call(
    attempt_id: uuid.UUID, call_id: str, user: ActiveUser, session: DbSession
) -> WebCallOut:
    """Keys of the browser call for a call on the card (issue #59): the cloud plays the duty
    officer or the squad leader. 409 when the lesson is not in the cloud mode or the calls
    go through telephony; 503 when the cloud is unreachable (the panel then keeps the text
    and microphone path)."""
    attempt, ts, card, scenario = await service_calls.card_attempt(session, attempt_id, user)
    if not dialog.cloud_lesson(ts):
        raise ApiError(
            409, "not_cloud_lesson", "В этом занятии службу и бригаду играет локальная модель."
        )
    if telephony.for_session(ts):
        raise ApiError(409, "telephony_active", "Звонок идёт через телефонию стенда.")
    web_calls = cloud_web.get_calls()
    if web_calls is None:
        raise ApiError(503, "cloud_unavailable", "Облачный голос не запущен.")
    record = officer.find_call(attempt, call_id)
    if record.get("ended_at"):
        raise ApiError(409, "call_ended", "Звонок уже завершён.")
    # «Ответил» до того, как облако заговорит: первую фразу скажет оно само.
    _, events = await officer.answer(
        session, attempt, ts, card.version, scenario, call_id, with_opening=False
    )
    await session.commit()
    await publish_events(events)
    try:
        start = await web_calls.start_service_call(
            LoadedCardAttempt(attempt, ts, card.version, scenario), call_id
        )
    except VapiError as exc:
        await web_calls.failed(attempt.id, f"start: {exc}")
        raise ApiError(
            503,
            "cloud_unavailable",
            f"Облачный голос недоступен: {exc}. Разговор продолжится локально.",
        ) from exc
    return WebCallOut(
        public_key=start.public_key,
        api_url=start.api_url,
        assistant_id=start.assistant_id,
        token=start.token,
    )


@router.post("/attempts/{attempt_id}/cloud-call/failed", response_model=DialogTurnOut | None)
async def web_call_failed(
    attempt_id: uuid.UUID, body: WebCallFailedIn, user: ActiveUser, session: DbSession
) -> DialogTurnOut | None:
    """The SDK could not start or lost the call: noted in the session log; the panel
    continues with the microphone. The caller greets from our own side now — in a working
    cloud call the greeting is spoken by Vapi and we store none (замечание 22.09.2026), so
    the answer carries the opening the panel has to play."""
    attempt, _ts, version, scenario = await dialog.dialog_attempt(
        session, attempt_id, user, for_write=True
    )
    opening = await dialog.ensure_opening(attempt, version, scenario, training.utcnow())
    await session.commit()
    web_calls = cloud_web.get_calls()
    if web_calls is not None:
        await web_calls.failed(attempt_id, body.reason[:200] or "browser")
    return _turn_out(0, opening) if opening else None
