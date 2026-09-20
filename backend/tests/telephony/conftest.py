"""Shared fixtures of the telephony tests: the stand-in ARI server and a call manager wired
to it (``tests.telephony.test_calls`` defines the helpers, this file the fixtures)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from app.config import get_settings
from app.telephony.ari import AriClient
from app.telephony.calls import CallManager
from tests.telephony.fake_ari import FakeAri


@pytest.fixture
async def fake_ari() -> AsyncIterator[FakeAri]:
    fake = FakeAri()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
async def manager(fake_ari: FakeAri, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CallManager]:
    settings = get_settings()
    monkeypatch.setattr(settings, "telephony_media_host", "127.0.0.1")
    monkeypatch.setattr(settings, "telephony_media_port_start", 13000)
    monkeypatch.setattr(settings, "telephony_media_port_end", 13010)
    monkeypatch.setattr(settings, "vad_model_path", None)
    ari = AriClient(f"http://127.0.0.1:{fake_ari.port}/ari", "trainer", "trainer", "trainer")
    manager = CallManager(ari)
    stop = asyncio.Event()
    task = asyncio.create_task(ari.run_events(manager.handle_event, stop))
    await fake_ari.wait_for_client()
    yield manager
    stop.set()
    await manager.shutdown()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await ari.aclose()
