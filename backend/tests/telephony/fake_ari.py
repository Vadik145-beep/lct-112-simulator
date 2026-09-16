"""A stand-in for Asterisk's ARI: the HTTP resources the trainer uses plus the WebSocket of
events, served by uvicorn on a free port inside the test loop. Every request is recorded;
the test drives the call by emitting events."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response


@dataclass
class Recorded:
    method: str
    path: str
    params: dict
    body: dict | None = None


@dataclass
class FakeAri:
    requests: list[Recorded] = field(default_factory=list)
    clients: list[WebSocket] = field(default_factory=list)
    playback_delay: float = 0.05
    app: FastAPI = field(default_factory=FastAPI)
    server: uvicorn.Server | None = None
    task: asyncio.Task | None = None
    port: int = 0

    def __post_init__(self) -> None:
        app = self.app
        fake = self

        @app.middleware("http")
        async def record(request: Request, call_next):
            body = None
            if request.method in ("POST", "PUT"):
                raw = await request.body()
                if raw:
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        body = None
            fake.requests.append(
                Recorded(request.method, request.url.path, dict(request.query_params), body)
            )
            return await call_next(request)

        @app.get("/ari/asterisk/info")
        async def info():
            return {"status": {"startup_time": "2026-09-19T00:00:00"}}

        @app.put("/ari/asterisk/modules/{module}")
        async def reload_module(module: str):
            return Response(status_code=204)

        @app.post("/ari/channels/create")
        async def create(request: Request):
            return {"id": request.query_params["channelId"], "state": "Down"}

        @app.post("/ari/channels/{channel_id}/variable")
        async def set_variable(channel_id: str):
            return Response(status_code=204)

        @app.post("/ari/channels/{channel_id}/dial")
        async def dial(channel_id: str):
            return Response(status_code=204)

        @app.post("/ari/channels/{channel_id}/answer")
        async def answer(channel_id: str):
            return Response(status_code=204)

        @app.delete("/ari/channels/{channel_id}")
        async def hangup(channel_id: str):
            return Response(status_code=204)

        @app.post("/ari/channels/{channel_id}/snoop")
        async def snoop(channel_id: str, request: Request):
            snoop_id = request.query_params["snoopId"]
            fake.schedule({"type": "StasisStart", "args": [], "channel": {"id": snoop_id}})
            return {"id": snoop_id}

        @app.post("/ari/channels/externalMedia")
        async def external_media(request: Request):
            media_id = request.query_params["channelId"]
            fake.schedule({"type": "StasisStart", "args": [], "channel": {"id": media_id}})
            return {"id": media_id, "channelvars": {"UNICASTRTP_LOCAL_PORT": "10000"}}

        @app.post("/ari/bridges")
        async def create_bridge(request: Request):
            return {"id": request.query_params["bridgeId"]}

        @app.post("/ari/bridges/{bridge_id}/addChannel")
        async def add_channel(bridge_id: str):
            return Response(status_code=204)

        @app.delete("/ari/bridges/{bridge_id}")
        async def destroy_bridge(bridge_id: str):
            return Response(status_code=204)

        @app.post("/ari/bridges/{bridge_id}/play")
        async def play(bridge_id: str, request: Request):
            playback_id = request.query_params["playbackId"]
            fake.schedule(
                {"type": "PlaybackFinished", "playback": {"id": playback_id}},
                delay=fake.playback_delay,
            )
            return JSONResponse({"id": playback_id}, status_code=201)

        @app.post("/ari/bridges/{bridge_id}/record")
        async def record_bridge(bridge_id: str, request: Request):
            return JSONResponse({"name": request.query_params["name"]}, status_code=201)

        @app.post("/ari/recordings/live/{name:path}/stop")
        async def stop_recording(name: str):
            return Response(status_code=204)

        @app.put("/ari/deviceStates/{device}")
        async def device_state(device: str):
            return Response(status_code=204)

        @app.websocket("/ari/events")
        async def events(socket: WebSocket):
            await socket.accept()
            fake.clients.append(socket)
            try:
                while True:
                    await socket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                if socket in fake.clients:
                    fake.clients.remove(socket)

    # ------------------------------------------------------------ life cycle

    async def start(self) -> str:
        config = uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="warning")
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:  # noqa: ASYNC110 - uvicorn exposes no event
            await asyncio.sleep(0.01)
        self.port = self.server.servers[0].sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{self.port}/ari"

    async def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True
        if self.task is not None:
            await self.task

    # ------------------------------------------------------------ events

    async def emit(self, event: dict) -> None:
        payload = json.dumps({"application": "trainer", **event})
        for client in list(self.clients):
            await client.send_text(payload)

    def schedule(self, event: dict, delay: float = 0.0) -> None:
        async def later():
            await asyncio.sleep(delay)
            await self.emit(event)

        asyncio.get_running_loop().create_task(later())

    async def wait_for_client(self, seconds: float = 5.0) -> None:
        deadline = asyncio.get_running_loop().time() + seconds
        while not self.clients:
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError("ARI client did not connect")
            await asyncio.sleep(0.02)

    def calls(self, method: str, path_part: str) -> list[Recorded]:
        return [r for r in self.requests if r.method == method and path_part in r.path]

    async def wait_for(self, method: str, path_part: str, seconds: float = 5.0) -> Recorded:
        deadline = asyncio.get_running_loop().time() + seconds
        while True:
            found = self.calls(method, path_part)
            if found:
                return found[-1]
            if asyncio.get_running_loop().time() > deadline:
                raise TimeoutError(f"no {method} {path_part}")
            await asyncio.sleep(0.02)
