"""Async wrapper around a single `pi --mode rpc` subprocess.

Responsibilities:
- spawn pi with the right cwd / env / session
- read JSONL events from stdout, hand them to a subscriber
- write JSON commands to stdin
- watchdog: respawn if the process goes silent or dies
- headless handling of extension-UI dialogs (auto-cancel)

Protocol ref: docs/rpc.md (JSON over stdio, LF framing).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

import config

log = logging.getLogger("pi.process")

# A subscriber receives every parsed stdout record (dict).
Subscriber = Callable[[dict], Awaitable[None]]

# Called (fire-and-forget) when the pi process dies or is killed by the watchdog.
DeadCallback = Callable[["PiProcess"], Awaitable[None]]

UI_METHODS_NEED_RESPONSE = {"select", "confirm", "input", "editor"}


class PiProcess:
    def __init__(
        self,
        project: str,
        subscriber: Subscriber,
        idle_timeout: float = config.PI_IDLE_TIMEOUT,
        on_dead: DeadCallback | None = None,
    ):
        self.project = project
        self.subscriber = subscriber
        self.idle_timeout = idle_timeout
        self.on_dead = on_dead

        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._last_activity = 0.0
        self._stopping = False  # True when stop() is in progress (no respawn)
        self._dead_fired = False
        self._write_lock = asyncio.Lock()
        self._active = False  # True between agent_start and agent_settled

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        project_dir = config.project_dir(self.project)
        project_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("uploads", "downloads"):
            (project_dir / sub).mkdir(exist_ok=True)

        sess_dir = config.SESSION_DIR / self.project
        sess_dir.mkdir(parents=True, exist_ok=True)
        session_file = config.latest_session(self.project)

        argv = [config.PI_BIN, "--mode", "rpc"]
        if session_file is not None and session_file.exists():
            argv += ["--session", str(session_file)]
            log.info("[pi:%s] resuming %s", self.project, session_file.name)
        else:
            log.info("[pi:%s] starting new session", self.project)

        env = dict(os.environ)
        env["PI_CODING_AGENT_DIR"] = str(config.AGENT_DIR)
        env["PI_CODING_AGENT_SESSION_DIR"] = str(sess_dir)
        # Make sure the LLM key reaches pi (it reads $SSP_LLM_KEY).
        env.setdefault(config.LLM_KEY_ENV, "")

        log.info("[pi:%s] spawn: %s (cwd=%s)", self.project, " ".join(argv), project_dir)
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(project_dir),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._last_activity = time.monotonic()
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"pi-read-{self.project}")
        self._watchdog_task = asyncio.create_task(self._watchdog(), name=f"pi-watch-{self.project}")
        asyncio.create_task(self._drain_stderr(), name=f"pi-err-{self.project}")

    async def stop(self) -> None:
        self._stopping = True
        for task in (self._reader_task, self._watchdog_task):
            if task:
                task.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except (TimeoutError, ProcessLookupError):
                self._proc.kill()

    # ------------------------------------------------------------------ #
    # Commands (write to stdin)
    # ------------------------------------------------------------------ #
    async def send(self, command: dict) -> None:
        if not self._proc or self._proc.stdin is None:
            raise RuntimeError("pi process not running")
        line = json.dumps(command, ensure_ascii=False) + "\n"
        async with self._write_lock:
            self._proc.stdin.write(line.encode("utf-8"))
            await self._proc.stdin.drain()
        log.debug("[pi:%s] -> %s", self.project, command.get("type"))

    # ------------------------------------------------------------------ #
    # Read loop
    # ------------------------------------------------------------------ #
    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout is not None
        while True:
            raw = await self._proc.stdout.readline()
            if not raw:  # EOF
                log.info("[pi:%s] stdout EOF", self.project)
                break
            self._last_activity = time.monotonic()
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning("[pi:%s] non-JSON stdout: %r", self.project, line[:200])
                continue
            await self._handle(record)
        # EOF reached. If we weren't told to stop, tell the manager we died.
        await self._signal_dead()

    async def _handle(self, record: dict) -> None:
        rtype = record.get("type")

        # Extension UI protocol.
        if rtype == "extension_ui_request":
            await self._handle_ui(record)
            return

        # Track the active window so the watchdog only enforces while working.
        if rtype == "agent_start":
            self._active = True
        elif rtype == "agent_settled":
            self._active = False

        # Everything else (events + command responses) goes to the subscriber.
        try:
            await self.subscriber(record)
        except Exception:
            log.exception("[pi:%s] subscriber error", self.project)

    async def _handle_ui(self, record: dict) -> None:
        method = record.get("method")
        # Fire-and-forget (notify, setStatus, setWidget, setTitle, set_editor_text):
        # forward to the client so the UI can show status, but never block.
        if method in UI_METHODS_NEED_RESPONSE:
            # Headless: auto-cancel so pi never blocks waiting for a dialog.
            log.info("[pi:%s] auto-cancelling UI dialog %s", self.project, method)
            with suppress(Exception):
                await self.send(
                    {"type": "extension_ui_response", "id": record.get("id"), "cancelled": True}
                )
        # Forward fire-and-forget status to the client.
        with suppress(Exception):
            await self.subscriber(record)

    async def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr is not None
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                break
            log.debug("[pi:%s stderr] %s", self.project, line.decode("utf-8", "replace").rstrip())

    async def _signal_dead(self) -> None:
        """Notify the manager exactly once that this process is gone."""
        if self._stopping or self._dead_fired:
            return
        self._dead_fired = True
        log.warning(
            "[pi:%s] process exited unexpectedly (rc=%s)",
            self.project,
            self._proc.returncode if self._proc else None,
        )
        if self.on_dead:
            try:
                await self.on_dead(self)
            except Exception:
                log.exception("[pi:%s] on_dead handler error", self.project)

    # ------------------------------------------------------------------ #
    # Watchdog
    # ------------------------------------------------------------------ #
    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(5)
            if self._stopping:
                return
            # Only watch while the agent is actually processing a turn.
            # A silent-but-idle pi (waiting for the next prompt) is normal.
            if not self._active:
                self._last_activity = time.monotonic()
                continue
            idle = time.monotonic() - self._last_activity
            # Mid-turn silence beyond idle_timeout => the LLM/tool is hung.
            if idle > self.idle_timeout:
                log.warning("[pi:%s] mid-turn stall %.0fs, killing", self.project, idle)
                if self._proc and self._proc.returncode is None:
                    self._proc.kill()
                # The read loop will hit EOF and call _signal_dead().
