"""The webhook of Vapi (plan/track-c-vapi.md): off unless the cloud voice is enabled, refuses
a wrong secret, and hands valid messages to the cloud call manager."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.config import get_settings
from app.telephony import service as telephony
from app.telephony.cloud import CloudCallManager
from app.telephony.vapi import SECRET_HEADER, WEBHOOK_PATH, webhook_secret


class StubService:
    def __init__(self, manager) -> None:
        self.calls = manager


class RecordingManager(CloudCallManager):
    def __init__(self) -> None:  # no ARI, no Vapi: only the webhook dispatch is exercised
        self.messages: list[dict] = []

    def call_for_message(self, message: dict):
        return None

    async def webhook(self, message: dict) -> dict:
        self.messages.append(message)
        return (
            {"assistant": {"firstMessage": "ok"}} if message["type"] == "assistant-request" else {}
        )


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> RecordingManager:
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", "hook-secret")
    manager = RecordingManager()
    monkeypatch.setattr(telephony, "_service", StubService(manager))
    return manager


async def test_disabled_cloud_voice_is_not_found(client: AsyncClient, monkeypatch):
    monkeypatch.setattr(get_settings(), "cloud_voice_enabled", False)
    response = await client.post(WEBHOOK_PATH, json={"message": {"type": "hang"}})
    assert response.status_code == 404


async def test_wrong_or_missing_secret_is_refused(client: AsyncClient, cloud: RecordingManager):
    body = {"message": {"type": "status-update", "status": "ended"}}
    assert (await client.post(WEBHOOK_PATH, json=body)).status_code == 401
    wrong = await client.post(WEBHOOK_PATH, json=body, headers={SECRET_HEADER: "nope"})
    assert wrong.status_code == 401
    assert cloud.messages == []


async def test_valid_message_reaches_the_manager(client: AsyncClient, cloud: RecordingManager):
    headers = {SECRET_HEADER: webhook_secret()}
    assert webhook_secret() == "hook-secret"
    response = await client.post(
        WEBHOOK_PATH,
        json={"message": {"type": "assistant-request", "call": {"id": "c1"}}},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json() == {"assistant": {"firstMessage": "ok"}}
    assert cloud.messages[-1]["type"] == "assistant-request"

    bad = await client.post(WEBHOOK_PATH, json={"nothing": 1}, headers=headers)
    assert bad.status_code == 400
    empty = await client.post(WEBHOOK_PATH, json={"message": {"type": "hang"}}, headers=headers)
    assert empty.status_code == 200 and empty.json() == {}


async def test_without_a_cloud_manager_messages_are_ignored(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "cloud_voice_enabled", True)
    monkeypatch.setattr(settings, "cloud_voice_webhook_secret", "hook-secret")
    monkeypatch.setattr(telephony, "_service", None)
    response = await client.post(
        WEBHOOK_PATH,
        json={"message": {"type": "transcript"}},
        headers={SECRET_HEADER: "hook-secret"},
    )
    assert response.status_code == 200 and response.json() == {}
