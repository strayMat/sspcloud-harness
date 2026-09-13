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

## Secrets (Onyxia vault)

| Env var | Purpose |
| --------- | --------- |
| `SSP_LLM_KEY` | LLM key |

Management in vault and taken at deployment-time with kube.

## Next

- [ ] Add some extensions to underlying py : pi-web-access, @juicesharp/rpiv-todo
- [ ] Render assistant `thinking` blocks (currently skipped)
- [ ] Show tool result output on click (currently just name + done/error)
- [ ] Delete-project button in UI (REST exists but no UI)
- [ ] Abort button (REST exists via WS `abort`, no button yet)
- [ ] Drag-and-drop upload
- [ ] check pi is in auto-approve mode to avoid stall discussion
- [ ] Usecase check passing: able to download from insee.fr a data table (eg. https://catalogue-donnees.insee.fr/fr/catalogue/recherche/DS_TOUR_FREQ) and plot a graph rendered to the user 

## Future: S3 persistence

- PVC is the stopgap for `workspace/ .pi-sessions/ .pi-agent/`.
- Long term: back it with the SSP Cloud S3/MinIO storage (service account
  - K8s secret, as in the shiny tutorial), so data survives pod rescheduling
