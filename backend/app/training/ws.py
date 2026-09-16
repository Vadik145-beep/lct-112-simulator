"""WebSocket of a training session (PRD section 12): ``/ws/sessions/{id}?after_seq=N``.

The first client message carries the access token (browsers cannot set headers on a
WebSocket). The server replays events after ``after_seq`` from the table, answers
``{"type": "ready", "seq": N}`` and then forwards live events from Redis. Students receive
shared events and their own; the teacher of the session receives everything.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app.db import SessionLocal
from app.errors import ApiError
from app.events import channel_for, event_to_dict, events_after, get_redis
from app.logging import get_logger
from app.models import Role, User
from app.security import decode_token
from app.training import service as training

log = get_logger(__name__)
router = APIRouter()

AUTH_TIMEOUT_SECONDS = 10
# Close codes: 4401 = not authenticated, 4404 = no such session for this user.
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404


async def _authenticate(ws: WebSocket) -> User | None:
    try:
        raw = await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT_SECONDS)
        message = json.loads(raw)
    except (TimeoutError, ValueError, WebSocketDisconnect):
        return None
    payload = decode_token(str(message.get("token") or ""), "access")
    if payload is None:
        return None
    async with SessionLocal() as session:
        user = await session.get(User, uuid.UUID(payload["sub"]))
        if user is None or user.is_blocked or user.token_version != payload.get("tv"):
            return None
        return user


def _visible(event: dict, user: User) -> bool:
    if user.role != Role.student:
        return True
    return event.get("student_id") in (None, str(user.id))


@router.websocket("/ws/sessions/{session_id}")
async def session_events(ws: WebSocket, session_id: uuid.UUID, after_seq: int = 0) -> None:
    await ws.accept()
    user = await _authenticate(ws)
    if user is None:
        await ws.close(code=CLOSE_UNAUTHORIZED, reason="Требуется вход в систему.")
        return
    async with SessionLocal() as session:
        try:
            await training.get_session_for(session, session_id, user)
        except ApiError:
            await ws.close(code=CLOSE_NOT_FOUND, reason="Занятие не найдено.")
            return
        student_id = user.id if user.role == Role.student else None
        missed = await events_after(session, session_id, after_seq, student_id=student_id)
    last = after_seq
    for event in missed:
        await ws.send_text(json.dumps(event_to_dict(event)))
        last = event.seq
    await ws.send_text(json.dumps({"type": "ready", "seq": last}))

    pubsub = get_redis().pubsub()
    try:
        await pubsub.subscribe(channel_for(session_id))
    except (OSError, aioredis.RedisError) as exc:
        log.warning("pubsub unavailable", error=str(exc))
        await ws.close(code=1011, reason="Служба событий недоступна.")
        return

    async def forward() -> None:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            event = json.loads(message["data"])
            if event.get("seq", 0) <= last or not _visible(event, user):
                continue
            await ws.send_text(message["data"])

    async def listen_client() -> None:
        while True:
            raw = await ws.receive_text()
            if raw == "ping" or (raw.startswith("{") and json.loads(raw).get("type") == "ping"):
                await ws.send_text(json.dumps({"type": "pong"}))

    tasks = [asyncio.create_task(forward()), asyncio.create_task(listen_client())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, WebSocketDisconnect | RuntimeError):
                log.warning("websocket task failed", error=str(exc))
    finally:
        for task in tasks:
            task.cancel()
        await pubsub.unsubscribe(channel_for(session_id))
        await pubsub.aclose()
        if ws.client_state == WebSocketState.CONNECTED:
            await ws.close()
