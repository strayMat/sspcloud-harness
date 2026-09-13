"""FastAPI app: WebSocket chat + project/file management.

Run locally with:
    uv run python -m app.main

Or from the project root:
    uv run uvicorn app.main:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import (
    Body,
    FastAPI,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import config
import pi_config
from app import auth
from session_manager import SessionManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("app")

manager = SessionManager()

# Default project: auto-create "default" on first boot if none exists.
DEFAULT_PROJECT = "default"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Fail fast: a pod without the key would only die on the first chat
    # message. Crash-loop loudly instead.
    if not os.environ.get(config.LLM_KEY_ENV):
        raise RuntimeError(
            f"{config.LLM_KEY_ENV} missing from environment; "
            "check the ssphub-harness secret (envFrom) in the chart"
        )
    config.ensure_layout()
    pi_config.seed_agent_dir()
    # Pre-create default project dir.
    config.project_dir(DEFAULT_PROJECT).mkdir(parents=True, exist_ok=True)
    log.info("layout ready: workspace=%s agent=%s", config.WORKSPACE_DIR, config.AGENT_DIR)
    yield
    await manager.shutdown()


app = FastAPI(title="SSPCloud Pi Harness", lifespan=lifespan)


class AuthMiddleware(BaseHTTPMiddleware):
    """Gate every /api and /static path behind the session cookie.

    /health and /login stay open so a bare curl / browser can reach login.
    WebSocket auth is handled separately in websocket_chat.
    """

    EXEMPT = frozenset({"/health", "/login", "/auth/status", "/"})

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        need_auth = auth.AUTH.enabled and path not in self.EXEMPT and not path.startswith("/ws")
        if need_auth and not auth.verify_token(request.cookies.get(auth.COOKIE_NAME, "")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


# ---------------------------------------------------------------------- #
# Project management
# ---------------------------------------------------------------------- #


def _list_projects() -> list[dict[str, Any]]:
    ws = config.WORKSPACE_DIR
    if not ws.exists():
        return []
    out = []
    for d in sorted(ws.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        sess = config.session_path(d.name)
        hist_lines = 0
        if sess.exists():
            with suppress(OSError), open(sess, "rb") as f:
                hist_lines = sum(1 for _ in f)
        out.append(
            {
                "name": d.name,
                "active": d.name == manager.project,
                "messages": hist_lines,
            }
        )
    return out


@app.get("/api/projects")
async def list_projects():
    return JSONResponse({"projects": _list_projects(), "active": manager.project})


@app.post("/api/projects")
async def create_project(payload: dict[str, Any]):
    name = config.sanitize(payload.get("name", ""))
    if not name:
        raise HTTPException(400, "name required")
    pd = config.project_dir(name)
    if pd.exists() and any(pd.iterdir()):
        raise HTTPException(409, f"project {name!r} already exists and is not empty")
    pd.mkdir(parents=True, exist_ok=True)
    (pd / "uploads").mkdir(exist_ok=True)
    (pd / "downloads").mkdir(exist_ok=True)
    return JSONResponse({"ok": True, "project": name})


@app.delete("/api/projects/{name}")
async def delete_project(name: str):
    name = config.sanitize(name)
    pd = config.project_dir(name)
    if manager.project == name:
        raise HTTPException(409, "cannot delete the active project; switch first")
    if pd.exists():
        try:
            shutil.rmtree(pd)
        except OSError as err:
            raise HTTPException(500, "could not delete project dir") from err
    sess = config.session_path(name)
    if sess.exists():
        sess.unlink()
    return JSONResponse({"ok": True})


@app.post("/api/projects/{name}/activate")
async def activate_project(name: str):
    name = config.sanitize(name)
    if not config.project_dir(name).exists():
        raise HTTPException(404, "project not found")
    await manager.activate(name)
    return JSONResponse({"ok": True, "active": name})


# ---------------------------------------------------------------------- #
# File management (uploads / downloads)
# ---------------------------------------------------------------------- #
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _safe_file_path(project: str, subdir: str, filename: str) -> Path:
    """Resolve a file path inside <project>/<subdir>, rejecting traversal."""
    project = config.sanitize(project)
    if subdir not in ("uploads", "downloads"):
        raise HTTPException(400, "invalid subdir")
    # Take only the basename; reject anything with separators or dot-paths.
    base = os.path.basename(filename)
    if not base or base in (".", ".."):
        raise HTTPException(400, "invalid filename")
    target = config.project_dir(project) / subdir / base
    # Ensure it stays within the subdir.
    try:
        target.resolve().relative_to((config.project_dir(project) / subdir).resolve())
    except ValueError as err:
        raise HTTPException(400, "invalid path") from err
    return target


@app.post("/api/projects/{name}/upload")
async def upload_file(name: str, file: UploadFile = File(...)):
    name = config.sanitize(name)
    if not config.project_dir(name).exists():
        raise HTTPException(404, "project not found")
    target = _safe_file_path(name, "uploads", file.filename or "upload.bin")
    target.parent.mkdir(parents=True, exist_ok=True)

    size = 0
    try:
        with open(target, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    f.close()
                    target.unlink(missing_ok=True)
                    raise HTTPException(413, f"file exceeds {MAX_UPLOAD_BYTES} bytes")
                f.write(chunk)
    except OSError as err:
        raise HTTPException(500, "write failed") from err
    log.info("uploaded %s -> %s (%d bytes)", file.filename, target, size)
    return JSONResponse({"ok": True, "path": f"uploads/{target.name}", "size": size})


def _list_files(name: str, subdir: str) -> list[dict[str, Any]]:
    name = config.sanitize(name)
    d = config.project_dir(name) / subdir
    if not d.exists():
        return []
    out = []
    for f in sorted(d.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            out.append(
                {
                    "name": f.name,
                    "path": f"{subdir}/{f.name}",
                    "size": f.stat().st_size,
                }
            )
    return out


@app.get("/api/projects/{name}/files")
async def list_files(name: str, subdir: str = Query("downloads", pattern="^(uploads|downloads)$")):
    return JSONResponse({"files": _list_files(name, subdir)})


@app.get("/api/projects/{name}/download/{path:path}")
async def download_file(name: str, path: str):
    name = config.sanitize(name)
    # path is like "downloads/report.md"
    parts = path.split("/", 1)
    if len(parts) != 2 or parts[0] not in ("uploads", "downloads"):
        raise HTTPException(400, "path must be <uploads|downloads>/<file>")
    target = _safe_file_path(name, parts[0], parts[1])
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(target, filename=target.name)


# ---------------------------------------------------------------------- #
# WebSocket chat
# ---------------------------------------------------------------------- #


@app.websocket("/ws")
async def websocket_chat(ws: WebSocket):
    if not auth.check_ws_cookie(ws):
        await ws.close(code=1008)  # policy violation: unauthenticated
        return
    await ws.accept()
    await manager.register(ws)
    try:
        # If no project is active yet, auto-activate the default.
        if manager.project is None:
            await manager.activate(DEFAULT_PROJECT)
            await ws.send_text(
                json.dumps({"type": "harness", "event": "activated", "project": manager.project})
            )

        async for raw in ws.iter_text():
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({"type": "error", "message": "invalid JSON"}))
                continue

            mtype = msg.get("type")
            if mtype == "prompt":
                text = msg.get("message", "")
                if not text.strip():
                    continue
                behavior = msg.get("streamingBehavior")
                try:
                    await manager.send_prompt(text, behavior)
                except RuntimeError as e:
                    await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            elif mtype == "abort":
                await manager.abort()
            elif mtype == "new_session":
                await manager.new_session()
            elif mtype == "get_state":
                await manager.get_state()
            elif mtype == "get_messages":
                await manager.get_messages()
            elif mtype == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))
            else:
                await ws.send_text(
                    json.dumps({"type": "error", "message": f"unknown type {mtype!r}"})
                )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(ws)


# ---------------------------------------------------------------------- #
# Health
# ---------------------------------------------------------------------- #
@app.get("/health")
async def health():
    return {"ok": True, "active_project": manager.project, "pi_running": manager.active}


# ---------------------------------------------------------------------- #
# Frontend (single-page chat)
# ---------------------------------------------------------------------- #
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((STATIC_DIR / "index.html").read_text())


# ---------------------------------------------------------------------- #
# Auth
# ---------------------------------------------------------------------- #
@app.get("/auth/status")
async def auth_status():
    return {"enabled": auth.AUTH.enabled}


@app.post("/login")
async def login(payload: dict[str, str] = Body(...)):
    password = payload.get("password", "")
    if not auth.check_password(password):
        raise HTTPException(401, "bad password")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(**auth.get_cookie(auth.make_token()))
    return resp


@app.post("/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
    return resp


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.add_middleware(AuthMiddleware)
