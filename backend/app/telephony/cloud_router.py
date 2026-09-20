"""Webhook of Vapi (plan/track-c-vapi.md): every server message of a cloud call lands here.
No user session: the request proves itself with the ``X-Trainer-Secret`` header the backend
put into the number's server configuration."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Request

from app.config import get_settings
from app.errors import ApiError
from app.telephony import service as telephony
from app.telephony.cloud import CloudCallManager
from app.telephony.vapi import SECRET_HEADER, WEBHOOK_PATH, webhook_secret

router = APIRouter(tags=["cloud-voice"])
API_PREFIX = "/api"
ROUTE_PATH = WEBHOOK_PATH.removeprefix(API_PREFIX)


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
    if not isinstance(manager, CloudCallManager):
        return {}
    return await manager.webhook(message)
