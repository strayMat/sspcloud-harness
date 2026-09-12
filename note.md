# SSPCloud Pi Harness — notes

## Status (local)

Working local prototype. Verified end-to-end:
- Chat over WebSocket → pi RPC → LLM (qwen3-8-27b via sspcloud) ✓
- File upload → pi `read` → pi `write` → download ✓
- Project isolation (fresh context per project) ✓
- **Persistence**: kill server → restart → pi resumes session, remembers
  earlier context ✓
- Ruff clean (check + format) ✓

Run: see README.md.

## What's built

```
app/main.py         FastAPI: WS chat + project/file REST endpoints + UI mount
session_manager.py  single active pi, WS fan-out, respawn-on-crash
pi_process.py       asyncio pi subprocess: JSONL I/O, watchdog, UI auto-cancel
pi_config.py        seeds isolated .pi-agent/{models,settings}.json
config.py           env-driven paths + latest-session lookup
static/index.html   single-page chat UI (projects, files, markdown, streaming)
test_ws.py          tiny WS smoke-test client
```

## Key design decisions (local)

- **Isolated pi**: `PI_CODING_AGENT_DIR=.pi-agent` so we never touch the
  developer's real `~/.pi/agent`. Models/settings are seeded from env.
- **Per-project sessions**: `PI_CODING_AGENT_SESSION_DIR=.pi-sessions/<project>`;
  on (re)start we pass `--session <newest .jsonl>`.
- **No MCP locally**: `pi-mcp-adapter` + `pi-web-access` are NOT wired in the
  isolated agent dir — they need the sspcloud-mcp service, which is an Onyxia
  service. Core coding agent (read/write/edit/bash) works standalone.

## Secrets (Onyxia → env vars)

| Env var | Purpose |
|---------|---------|
| `SSP_LLM_KEY` | LLM key (`$SSP_LLM_KEY` in models.json) |
| `SSPCLOUD_MCP_URL` | sspcloud-mcp HTTPS URL |
| `SSPCLOUD_MCP_BEARER` | sspcloud-mcp bearer token |

The sspcloud-mcp is a *separate* Onyxia service (its own Deployment). The
harness talks to it over HTTP with the bearer header via pi-mcp-adapter.

## Known bugs / to fix

- [x] **File upload/download UI** — files panel now shows both `uploads/` and
  `downloads/` with an upload button (multipart POST). Each project scoped.
- [x] **Chat history on page reload** — on WS connect the frontend sends
  `get_messages` to pi and renders the returned messages (user, assistant text
  blocks, tool calls as cards). Also re-loads on project switch.

## Remaining UI polish (lower priority)

- [ ] Add some extensions to underlying py : pi-web-access, @juicesharp/rpiv-todo
- [ ] Render assistant `thinking` blocks (currently skipped)
- [ ] Show tool result output on click (currently just name + done/error)
- [ ] Delete-project button in UI (REST exists but no UI)
- [ ] Abort button (REST exists via WS `abort`, no button yet)
- [ ] Drag-and-drop upload

## Deploy TODO (Onyxia)

1. [ ] **Dockerfile**: python:3.12 + node:22, `pi install npm:pi-mcp-adapter`,
      bake `.pi-agent` config.
2. [ ] **Startup script**: generate `.mcp.json` (or set the MCP server) using
      `SSPCLOUD_MCP_URL` / `SSPCLOUD_MCP_BEARER`; confirm pi-mcp-adapter picks
      it up in RPC mode. (Check whether it supports env interpolation in the
      header, else generate the file at boot.)
3. [ ] **K8s / Onyxia service def**: permanent Deployment, PVC mounted for
      `workspace/ .pi-sessions/ .pi-agent/`, liveness on `/health`, port 8080.
4. [ ] **Auth**: Onyxia bearer in front of the gateway (single-user namespace).
5. [ ] **E2E on cluster**: chat + sspcloud compute tools (e.g. run a notebook).

## Open / watch

- **MCP in RPC mode**: need to confirm pi-mcp-adapter loads + connects to the
  HTTP MCP server when pi runs headless (`--mode rpc`). It emitted a
  `setStatus(mcp)` extension request locally (fire-and-forget) — looks like it
  was active, but that was against the *global* agent dir. Re-verify with the
  isolated dir + real MCP URL.
- **Tool approval**: some MCP tools may prompt for approval (a dialog). In
  headless RPC we auto-cancel dialogs — verify sspcloud compute tools don't
  require one, or configure auto-approve.
- **Concurrent WS**: single active project; a second client just joins the
  same stream (fine for single-user). No per-client session isolation yet.
- **Upload size / concurrency limits**: 50 MB cap, no per-user rate limit.
