"""WebSocket of a session: token in the first message, replay after ``after_seq``, live
events through Redis, access checks."""

import asyncio
import json
import socket
from collections.abc import AsyncIterator

import pytest
import uvicorn
import websockets
from httpx import AsyncClient

from tests.api.conftest import DATA_DIR
from tests.api.test_attempts import journal, make_session, set_status
from tests.conftest import bearer, login

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "seed" / "classifier.json").exists(),
    reason="нет data/seed/classifier.json",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
async def server_url() -> AsyncIterator[str]:
    """Real uvicorn server in the test loop (httpx cannot speak WebSocket)."""
    from app.main import app

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):  # up to 5 s for the socket to open
        if server.started:
            break
        await asyncio.sleep(0.05)
    yield f"ws://127.0.0.1:{port}"
    server.should_exit = True
    await task


RECV_TIMEOUT_SECONDS = 5


async def _recv(ws: websockets.ClientConnection) -> dict:
    return json.loads(await asyncio.wait_for(ws.recv(), RECV_TIMEOUT_SECONDS))


async def test_replay_then_live_events(client: AsyncClient, server_url: str) -> None:
    session_id = await make_session()
    token = await login(client, "student1")
    attempt_id = (await journal(client, token, session_id))["items"][0]["attempt_id"]
    await client.post(f"/api/attempts/{attempt_id}/open", headers=bearer(token))

    async with websockets.connect(f"{server_url}/ws/sessions/{session_id}?after_seq=1") as ws:
        await ws.send(json.dumps({"token": token["access_token"]}))
        replayed = await _recv(ws)
        assert replayed["seq"] == 2 and replayed["type"] == "attempt.received"
        ready = await _recv(ws)
        assert ready == {"type": "ready", "seq": 2}

        r = await set_status(client, token, attempt_id, status="accepted")
        assert r.status_code == 200
        live = await _recv(ws)
        assert live["type"] == "attempt.status_changed"
        assert live["seq"] == 3
        assert live["payload"]["status"] == "accepted"

        await ws.send(json.dumps({"type": "ping"}))
        assert await _recv(ws) == {"type": "pong"}


async def test_bad_token_and_foreign_session_are_closed(
    client: AsyncClient, server_url: str
) -> None:
    session_id = await make_session()
    async with websockets.connect(f"{server_url}/ws/sessions/{session_id}") as ws:
        await ws.send(json.dumps({"token": "nope"}))
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await _recv(ws)
        assert exc.value.rcvd.code == 4401

    stranger = await login(client, "student7")
    async with websockets.connect(f"{server_url}/ws/sessions/{session_id}") as ws:
        await ws.send(json.dumps({"token": stranger["access_token"]}))
        with pytest.raises(websockets.ConnectionClosed) as exc:
            await _recv(ws)
        assert exc.value.rcvd.code == 4404
