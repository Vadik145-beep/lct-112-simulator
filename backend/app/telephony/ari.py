"""Asterisk REST Interface client: HTTP commands plus the WebSocket of events of one Stasis
application. Only the handful of calls the trainer needs (PRD 9.5); the event loop reconnects
by itself and reports its state so the API can show «телефония недоступна».
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

from app.logging import get_logger

log = get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 20.0
RECONNECT_DELAYS_SECONDS = (1, 2, 5, 10)
EventHandler = Callable[[dict], Awaitable[None]]


class AriError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"ARI {status}: {message}")
        self.status = status


class AriClient:
    def __init__(self, base_url: str, user: str, password: str, app: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.app = app
        self._auth = (user, password)
        self._http = httpx.AsyncClient(
            base_url=self.base_url, auth=self._auth, timeout=REQUEST_TIMEOUT_SECONDS
        )
        self.connected = False
        self._tasks: set[asyncio.Task] = set()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------ plumbing

    async def request(
        self, method: str, path: str, params: dict | None = None, body: dict | None = None
    ) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            response = await self._http.request(method, path, params=clean, json=body)
        except httpx.HTTPError as exc:
            raise AriError(0, f"нет связи с Asterisk: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json().get("message", response.text)
            except ValueError:
                message = response.text
            raise AriError(response.status_code, message)
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    async def ping(self) -> bool:
        try:
            await self.request("GET", "/asterisk/info", {"only": "status"})
            return True
        except AriError:
            return False

    def events_url(self) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        host_path = self.base_url.split("://", 1)[1]
        query = urlencode(
            {
                "app": self.app,
                "api_key": f"{self._auth[0]}:{self._auth[1]}",
                "subscribeAll": "false",
            }
        )
        return f"{scheme}://{host_path}/events?{query}"

    @staticmethod
    async def _handle(handler: EventHandler, event: dict) -> None:
        try:
            await handler(event)
        except Exception:  # one bad event must not kill the loop
            log.exception("ari event handler failed", type=event.get("type"))

    async def run_events(self, handler: EventHandler, stop: asyncio.Event) -> None:
        """Delivers every event of the application to ``handler`` until ``stop`` is set,
        reconnecting after a pause when the link drops."""
        attempt = 0
        while not stop.is_set():
            try:
                async with ws_connect(self.events_url(), max_size=4 * 1024 * 1024) as socket:
                    self.connected = True
                    attempt = 0
                    log.info("ari connected", app=self.app)
                    async for raw in socket:
                        if stop.is_set():
                            break
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        # Handlers wait for other events (a playback to finish), so each one
                        # runs in its own task and never blocks the reader.
                        task = asyncio.create_task(self._handle(handler, event))
                        self._tasks.add(task)
                        task.add_done_callback(self._tasks.discard)
            except (TimeoutError, OSError, WebSocketException) as exc:
                if not stop.is_set():
                    log.warning("ari disconnected", error=str(exc))
            finally:
                self.connected = False
            if stop.is_set():
                break
            delay = RECONNECT_DELAYS_SECONDS[min(attempt, len(RECONNECT_DELAYS_SECONDS) - 1)]
            attempt += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except TimeoutError:
                pass

    # ------------------------------------------------------------ channels

    async def create_channel(
        self,
        endpoint: str,
        channel_id: str,
        app_args: str,
        formats: str | None = None,
        variables: dict[str, str] | None = None,
    ) -> dict:
        return await self.request(
            "POST",
            "/channels/create",
            {
                "endpoint": endpoint,
                "app": self.app,
                "appArgs": app_args,
                "channelId": channel_id,
                "formats": formats,
            },
            {"variables": variables} if variables else None,
        )

    async def set_variable(self, channel_id: str, variable: str, value: str) -> None:
        await self.request(
            "POST",
            f"/channels/{channel_id}/variable",
            {"variable": variable, "value": value},
        )

    async def dial(self, channel_id: str, ring_seconds: int) -> None:
        await self.request("POST", f"/channels/{channel_id}/dial", {"timeout": ring_seconds})

    async def answer(self, channel_id: str) -> None:
        await self.request("POST", f"/channels/{channel_id}/answer")

    async def hangup(self, channel_id: str, reason: str = "normal") -> None:
        try:
            await self.request("DELETE", f"/channels/{channel_id}", {"reason": reason})
        except AriError as exc:
            if exc.status != 404:  # already gone
                raise

    async def snoop(self, channel_id: str, snoop_id: str, app_args: str, spy: str = "in") -> dict:
        return await self.request(
            "POST",
            f"/channels/{channel_id}/snoop",
            {
                "spy": spy,
                "whisper": "none",
                "app": self.app,
                "appArgs": app_args,
                "snoopId": snoop_id,
            },
        )

    async def external_media(
        self, channel_id: str, host: str, port: int, app_args: str, fmt: str = "slin16"
    ) -> dict:
        return await self.request(
            "POST",
            "/channels/externalMedia",
            {
                "app": self.app,
                "external_host": f"{host}:{port}",
                "format": fmt,
                "encapsulation": "rtp",
                "transport": "udp",
                "connection_type": "client",
                "direction": "both",
                "channelId": channel_id,
                "appArgs": app_args,
            },
        )

    async def channel_variable(self, channel_id: str, variable: str) -> str | None:
        try:
            data = await self.request(
                "GET", f"/channels/{channel_id}/variable", {"variable": variable}
            )
        except AriError:
            return None
        return (data or {}).get("value")

    # ------------------------------------------------------------ bridges

    async def create_bridge(self, bridge_id: str, kind: str = "mixing") -> dict:
        return await self.request(
            "POST", "/bridges", {"type": kind, "bridgeId": bridge_id, "name": bridge_id}
        )

    async def add_channel(self, bridge_id: str, channel_id: str) -> None:
        await self.request("POST", f"/bridges/{bridge_id}/addChannel", {"channel": channel_id})

    async def destroy_bridge(self, bridge_id: str) -> None:
        try:
            await self.request("DELETE", f"/bridges/{bridge_id}")
        except AriError as exc:
            if exc.status != 404:
                raise

    async def play_bridge(self, bridge_id: str, media: str, playback_id: str) -> dict:
        return await self.request(
            "POST",
            f"/bridges/{bridge_id}/play",
            {"media": media, "playbackId": playback_id},
        )

    async def play_channel(self, channel_id: str, media: str, playback_id: str) -> dict:
        return await self.request(
            "POST",
            f"/channels/{channel_id}/play",
            {"media": media, "playbackId": playback_id},
        )

    async def stop_playback(self, playback_id: str) -> None:
        try:
            await self.request("DELETE", f"/playbacks/{playback_id}")
        except AriError as exc:
            if exc.status != 404:
                raise

    async def record_bridge(self, bridge_id: str, name: str, fmt: str = "wav") -> dict:
        return await self.request(
            "POST",
            f"/bridges/{bridge_id}/record",
            {"name": name, "format": fmt, "ifExists": "overwrite", "beep": "false"},
        )

    async def stop_recording(self, name: str) -> None:
        try:
            await self.request("POST", f"/recordings/live/{name}/stop")
        except AriError as exc:
            if exc.status != 404:
                raise

    # ------------------------------------------------------------ system

    async def reload_module(self, module: str) -> None:
        await self.request("PUT", f"/asterisk/modules/{module}")

    async def set_device_state(self, device: str, state: str) -> None:
        await self.request("PUT", f"/deviceStates/{device}", {"deviceState": state})

    async def endpoint(self, tech: str, resource: str) -> dict | None:
        try:
            return await self.request("GET", f"/endpoints/{tech}/{resource}")
        except AriError:
            return None
