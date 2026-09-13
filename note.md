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
| --------- | --------- |
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

## Deploy plan (Onyxia) — artifacts written, deploy pending

Decided:

- Repo: `git.lab.sspcloud.fr/mdoutrel/sspcloud_harness` (GitLab → `.gitlab-ci.yml`).
- Registry: **DockerHub**; all secrets (DOCKERHUB_USERNAME, DOCKERHUB_TOKEN,
  SSP_LLM_KEY) live in one Onyxia vault secret `SSPCLOUD_HARNESS`
  (KV v2 @ `onyxia-kv/user-mdoutrel/SSPCLOUD_HARNESS`).
- Ingress: `harness.ai-tools.ssp.cloud` (confirm on deploy; instance pattern
  is usually `*.lab.sspcloud.fr`).
- Cluster access: VSCode service with K8s **admin** role (kubectl + helm).

Secret plumbing (SSP_LLM_KEY), mirrors the S3 tutorial pattern:

- Single source of truth: Onyxia vault secret `SSPCLOUD_HARNESS`
  (`onyxia-kv/user-mdoutrel/SSPCLOUD_HARNESS`). Never hardcoded, never in
  Git, never re-typed.
- Onyxia's automatic vault→env injection (service config → Vault tab) only
  applies to UI-launched services — a helm-deployed app is NOT one.
  So: at deploy time, copy vault → K8s secret from a VSCode service (which
  has VAULT_ADDR/VAULT_TOKEN + vault CLI):
    kubectl create secret generic ssphub-harness \
      --from-literal=SSP_LLM_KEY="$(vault kv get -field=SSP_LLM_KEY \
        "$VAULT_MOUNT/$VAULT_TOP_DIR/SSPCLOUD_HARNESS")" -n <ns> \
      --dry-run=client -o yaml | kubectl apply -f -
- Chart: envFrom secretRef ssphub-harness → pod env → pi subprocess →
  "$SSP_LLM_KEY" interpolation in models.json (verified end-to-end).
- Rotation: re-run the kubectl command + `kubectl rollout restart
  deployment/<release>`. Pod env is fixed at pod start.
- App fail-fast: lifespan raises if SSP_LLM_KEY is missing, so a missing
  secret crash-loops loudly instead of failing on first chat.
- GitLab CI (DockerHub push) is NOT an Onyxia service: copy the 2 DockerHub
  values into GitLab project CI/CD variables (Settings → CI/CD → Variables,
  masked). Native `secrets:vault` (GitLab ≥17.11) is the cleaner
  alternative if the platform has it enabled.
- If the GitLab runner can't run docker (dind), fallback: `docker build &&
  docker push` from the Onyxia VSCode service (docker is available there,
  token read from the vault secret).

Artifacts in app repo (`git.lab.sspcloud.fr/mdoutrel/sspcloud_harness`):

- `Dockerfile` — python:3.13-slim + node 22 + pi (npm global), uv deps.
  Built and smoke-tested: /health 200, UI 200, pi subprocess spawns
  (pi_running:true).
  Gotcha hit: this machine's Docker daemon 27.2.1 flattens directory COPYs
  (`COPY app ./` → contents in `.`). Fixed with trailing-slash form
  `COPY app/ ./app/` — works on any daemon.
  Gotcha 2: `uv run` at boot re-syncs dev deps (needs network); CMD runs
  `/app/.venv/bin/uvicorn` directly, with `PYTHONPATH=/app`.
- `.gitlab-ci.yml` — build+push to DockerHub on main.
- **No Helm chart in this repo**: a SEPARATE deployment repo (mirrors the
  template-shiny-deployment pattern) holds the chart: Deployment (Recreate
  strategy, probes on /health, envFrom secret `ssphub-harness`), Service,
  Ingress, PVC 5Gi mounted at /data (→ PI_AGENT_DIR/PI_SESSION_DIR/
  HARNESS_WORKSPACE).

Remaining steps:

1. [ ] Push app repo to git.lab.sspcloud.fr; check a runner exists
      (Settings → CI/CD → Runners). If not, build+push from VSCode service.
2. [ ] Set GitLab CI/CD variables DOCKERHUB_USERNAME/DOCKERHUB_TOKEN
      (values from vault secret SSPCLOUD_HARNESS).
3. [ ] Create the deployment repo with the Helm chart (see "Chart spec"
      above); push it, clone from the VSCode admin service.
4. [ ] On the VSCode admin service: `kubectl create secret docker-registry
      dockerhub ...` and `kubectl create secret generic ssphub-harness
      --from-literal=SSP_LLM_KEY=...` in your namespace.
5. [ ] Fill the deployment repo's `values.yaml` (image repo, pullSecret,
      hostname, envSecret) → `helm install`.
6. [ ] E2E: chat + upload/download on the deployed URL.

v1 scope: no sspcloud-mcp (no documented server found); `.mcp.json`
wiring stays as a later step, gated on SSPCLOUD_MCP_URL/Bearer being set.

Chart spec (for the deployment repo):

```yaml
# values.yaml
image:
  repository: <DOCKERHUB_USERNAME>/sspcloud-harness   # CI image
  tag: latest
  pullPolicy: Always
  pullSecret: dockerhub
ingress:
  enabled: true
  hostname: harness.ai-tools.ssp.cloud
envSecret: ssphub-harness      # holds SSP_LLM_KEY
persistence:
  size: 5Gi
  existingClaim: ""           # skip PVC creation when set
replicas: 1
resources: {requests: {cpu: 200m, memory: 512Mi},
            limits: {memory: 2Gi}}
```

Templates (4 resources, all validated with helm v3.16.4):

- Deployment: Recreate strategy (1 replica + 1 PVC), containerPort 8080,
  envFrom secretRef `envSecret`, readiness on /health (5s/10s),
  liveness on /health (30s/30s), volume `data` → /data (RWO PVC
  `<release>-data`).
- Service: 8080 → containerPort.
- Ingress: `ingress.hostname` → `/` → service (Prefix).
- PVC: RWO `persistence.size`, skipped when `existingClaim` set.

## Future: S3 persistence

- PVC is the stopgap for `workspace/ .pi-sessions/ .pi-agent/`.
- Long term: back it with the SSP Cloud S3/MinIO storage (service account
  - K8s secret, as in the shiny tutorial), so data survives pod rescheduling
  and is user-managed.

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
