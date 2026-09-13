# SSPCloud Pi Harness

Claude-code-like remote harness: a **pi** agent runs as a JSON-RPC subprocess,
exposed through a **FastAPI** chat UI (WebSocket) with per-project file
upload/download. Built to run locally now and deploy to an Onyxia permanent
service later.

## Architecture

```
Browser (chat UI)
      │ WebSocket /ws  +  REST /api/*
      ▼
FastAPI app  (app/main.py)
      │ spawn / JSON-RPC over stdio
      ▼
pi --mode rpc   (pi_process.py, one subprocess for the active project)
      ├── built-in tools: read, write, edit, bash
      └── pi-mcp-adapter ──► sspcloud-mcp  (compute: code, GPU, notebooks)
                            (wired at deploy time; needs the Onyxia service)

On disk (persistent):
  workspace/<project>/uploads/      ← user uploads (pi can read)
  workspace/<project>/downloads/    ← pi writes (user downloads)
  .pi-sessions/<project>/*.jsonl    ← pi session history (auto-resumed)
  .pi-agent/                        ← isolated pi config (models, settings)
```

## Quick start (local)

```bash
# deps
uv sync

# model API key (Onyxia injects this as an env var in prod)
export SSP_LLM_KEY=...

# optional: password-protect the UI (unset = no auth, local dev mode)
export HARNESS_PASSWORD=...

# run
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080
# open http://localhost:8080
```

Quick smoke test of the chat channel (in another shell):

```bash
uv run python test_ws.py "What is 2+2?"
```

## Configuration (env vars)

| Var | Default | Purpose |
| ----- | --------- | --------- |
| `SSP_LLM_KEY` | – | LLM API key (required; used as `$SSP_LLM_KEY` in models.json) |
| `HARNESS_PASSWORD` | – | UI password; unset = auth disabled (local dev) |
| `HARNESS_AUTH_SECRET` | = password | HMAC secret for session cookies |
| `SSP_PROVIDER` | `sspcloud` | LLM provider id |
| `SSP_MODEL` | `qwen3-8-27b` | default model id |
| `PI_BIN` | `pi` | path to the pi executable |
| `PI_AGENT_DIR` | `.pi-agent/` | isolated pi config dir |
| `PI_SESSION_DIR` | `.pi-sessions/` | session storage |
| `HARNESS_WORKSPACE` | `workspace/` | project workspaces |
| `PI_IDLE_TIMEOUT` | `180` | mid-turn stall watchdog (seconds) |
| `SSPCLOUD_MCP_URL` | – | sspcloud-mcp URL (deploy time) |
| `SSPCLOUD_MCP_BEARER` | – | sspcloud-mcp bearer (deploy time) |

## REST API

| Method | Path | Purpose |
| -------- | ------ | --------- |
| GET | `/` | chat UI |
| GET | `/health` | liveness probe |
| GET | `/api/projects` | list projects (+active) |
| POST | `/api/projects` | create `{name}` |
| POST | `/api/projects/{name}/activate` | switch active project (respawns pi) |
| DELETE | `/api/projects/{name}` | delete project |
| POST | `/api/projects/{name}/upload` | multipart upload → `uploads/` |
| GET | `/api/projects/{name}/files?subdir=uploads\|downloads` | list files |
| GET | `/api/projects/{name}/download/<subdir>/<file>` | download a file |

## WebSocket protocol (`/ws`)

Client → server: `{"type":"prompt","message":"..."}`, `{"type":"abort"}`,
`{"type":"new_session"}`, `{"type":"get_state"}`, `{"type":"ping"}`.

Server → client: the raw pi RPC event stream (see `docs/rpc.md`) plus a few
harness-level records:

- `{"type":"harness","event":"activated","project":...}` on (re)connect
- `{"type":"harness","event":"pi_restarted","project":...}` after a crash respawn
- `{"type":"error","message":...}` for bad input

The UI streams `message_update` text deltas, shows `tool_execution_*` events,
and refreshes the download list on `agent_settled`.

## Resilience

- **Crash recovery**: pi session files are appended per message. If the pi
  subprocess dies, the manager respawns it with `--session <latest>` for the
  active project, so the chat resumes where it left off.
- **Stall watchdog**: while the agent is mid-turn (`agent_start`→`agent_settled`),
  no stdout for `PI_IDLE_TIMEOUT` seconds ⇒ kill + respawn.
- **WS reconnect**: the client auto-reconnects; the server re-broadcasts state.
- **Liveness**: `/health` is a fast probe (does not depend on pi).

## Deploy to Onyxia (TODO)

1. Dockerfile: `python:3.12` + `node:22` (for pi) + pi packages.
2. Install `pi-mcp-adapter` into the image's agent dir and point it at
   `SSPCLOUD_MCP_URL` / `SSPCLOUD_MCP_BEARER` (generate `.mcp.json` at startup).
3. Onyxia secrets → env vars (`SSP_LLM_KEY`, `SSPCLOUD_MCP_*`).
4. Permanent Deployment (auto-restart, no auto-suspend) + PVC for
   `workspace/`, `.pi-sessions/`, `.pi-agent/`.
5. Expose port 8080 via the Onyxia HTTPS gateway.

See `note.md` for the full design discussion.
