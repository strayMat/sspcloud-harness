"""Path & environment configuration for the SSPCloud pi harness.

Everything is overridable via environment variables so the same code runs
locally and (later) inside an Onyxia pod with secrets injected as env vars.
"""

from __future__ import annotations

import os
from pathlib import Path

# Root of the running instance (repo root by default).
BASE_DIR = Path(os.environ.get("HARNESS_DIR", Path(__file__).resolve().parent))

# Where the pi agent's isolated config dir lives (models, settings, packages).
# Isolated from the developer's real ~/.pi/agent on purpose.
AGENT_DIR = Path(os.environ.get("PI_AGENT_DIR", BASE_DIR / ".pi-agent"))

# Where per-project session files (.jsonl) live.
SESSION_DIR = Path(os.environ.get("PI_SESSION_DIR", BASE_DIR / ".pi-sessions"))

# Where project workspaces live.
WORKSPACE_DIR = Path(os.environ.get("HARNESS_WORKSPACE", BASE_DIR / "workspace"))

# pi executable (overridable; defaults to whatever is on PATH).
PI_BIN = os.environ.get("PI_BIN", "pi")

# Model / provider (mirror of ~/.pi/agent/models.json).
PROVIDER = os.environ.get("SSP_PROVIDER", "sspcloud")
MODEL_ID = os.environ.get("SSP_MODEL", "qwen3-8-27b")

# LLM API key — injected by the environment (Onyxia secret in prod).
LLM_KEY_ENV = "SSP_LLM_KEY"

# sspcloud-mcp service (deploy-time; optional locally).
SSPCLOUD_MCP_URL = os.environ.get("SSPCLOUD_MCP_URL", "")
SSPCLOUD_MCP_BEARER = os.environ.get("SSPCLOUD_MCP_BEARER", "")

# Watchdog: kill & respawn pi if no output for this many seconds.
PI_IDLE_TIMEOUT = float(os.environ.get("PI_IDLE_TIMEOUT", "180"))


def project_dir(name: str) -> Path:
    return WORKSPACE_DIR / name


def session_dir(name: str) -> Path:
    return SESSION_DIR / name


def latest_session(name: str) -> Path | None:
    """Return the most recent .jsonl session file for a project, if any.

    pi names session files with a timestamp + UUID (e.g.
    ``2026-09-12T09-01-04_01a094d9-....jsonl``), so we pick the newest by
    name (the timestamp prefix sorts chronologically).
    """
    d = session_dir(name)
    if not d.exists():
        return None
    files = [f for f in d.glob("*.jsonl") if f.is_file()]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def session_path(name: str) -> Path:
    """Backwards-compatible: returns the latest session file path (may not exist)."""
    p = latest_session(name)
    return p if p else SESSION_DIR / f"{name}.jsonl"


def ensure_layout() -> None:
    """Create the on-disk layout if it doesn't exist yet."""
    for d in (AGENT_DIR, SESSION_DIR, WORKSPACE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def sanitize(name: str) -> str:
    """Allow only a safe project name (no path traversal)."""
    name = name.strip()
    if not name or "/" in name or name in (".", ".."):
        raise ValueError(f"invalid project name: {name!r}")
    return name
