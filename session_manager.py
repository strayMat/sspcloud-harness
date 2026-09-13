"""Manages the single active pi process and fans its events out to the
connected WebSocket client(s).

There is exactly one conversation at a time (per the requirements). Multiple
projects exist on disk; switching projects respawns pi against the new
project's session file.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import WebSocket

import config
from pi_process import PiProcess

log = logging.getLogger("session.manager")


class SessionManager:
    def __init__(self) -> None:
        self._proc: PiProcess | None = None
        self._project: str | None = None
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._req_id = 0

    # ------------------------------------------------------------------ #
    # Project control
    # ------------------------------------------------------------------ #
    @property
    def project(self) -> str | None:
        return self._project

    @property
    def active(self) -> bool:
        return (
            self._proc is not None
            and self._proc._proc is not None
            and self._proc._proc.returncode is None
        )

    async def activate(self, name: str) -> None:
        """Spawn (or respawn) pi for the given project."""
        name = config.sanitize(name)
        async with self._lock:
            if self._project == name and self.active:
                return
            log.info("activating project %s", name)
            if self._proc:
                await self._proc.stop()
                self._proc = None
            self._project = name
            self._proc = PiProcess(name, subscriber=self._on_event, on_dead=self._on_dead)
            await self._proc.start()

    async def shutdown(self) -> None:
        if self._proc:
            await self._proc.stop()
            self._proc = None

    # ------------------------------------------------------------------ #
    # Commands -> pi
    # ------------------------------------------------------------------ #
    def _next_id(self) -> str:
        self._req_id += 1
        return f"h-{self._req_id}-{uuid.uuid4().hex[:6]}"

    async def send_prompt(self, message: str, streaming_behavior: str | None = None) -> str:
        cmd: dict[str, Any] = {"id": self._next_id(), "type": "prompt", "message": message}
        if streaming_behavior:
            cmd["streamingBehavior"] = streaming_behavior
        proc = await self._ensure_proc()
        await proc.send(cmd)
        return cmd["id"]

    async def abort(self) -> None:
        proc = await self._ensure_proc()
        await proc.send({"type": "abort"})

    async def new_session(self) -> None:
        proc = await self._ensure_proc()
        await proc.send({"type": "new_session"})

    async def get_state(self) -> None:
        proc = await self._ensure_proc()
        await proc.send({"id": self._next_id(), "type": "get_state"})

    async def get_messages(self) -> None:
        """Ask pi for the full conversation; the response is broadcast to all
        clients (used by the frontend to rehydrate the chat on page reload).
        """
        proc = await self._ensure_proc()
        await proc.send({"id": self._next_id(), "type": "get_messages"})

    # ------------------------------------------------------------------ #
    # WebSocket fan-out
    # ------------------------------------------------------------------ #
    async def register(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    async def unregister(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    async def _on_event(self, record: dict) -> None:
        payload = json_dumps(record)
        dead: list[WebSocket] = []
        for ws in list(self._clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    async def _on_dead(self, proc: PiProcess) -> None:
        # Respawn for the same project so the user's chat survives a crash.
        if self._project is None:
            return
        log.warning("respawning pi for %s", self._project)
        await self._broadcast(
            {"type": "harness", "event": "pi_restarted", "project": self._project}
        )
        async with self._lock:
            if self._proc is proc:
                self._proc = None
                self._proc = PiProcess(
                    self._project, subscriber=self._on_event, on_dead=self._on_dead
                )
                await self._proc.start()

    async def _broadcast(self, record: dict) -> None:
        await self._on_event(record)

    async def _ensure_proc(self) -> PiProcess:
        if not self.active:
            if self._project is None:
                raise RuntimeError("no active project")
            await self.activate(self._project)
        proc = self._proc
        if proc is None:
            raise RuntimeError("no active pi process")
        return proc


def json_dumps(record: dict) -> str:
    import json

    return json.dumps(record, ensure_ascii=False)
