# SSPCloud Pi Harness — deploy image.
# python:3.13 (pyproject requires >=3.13) + node 22 for the pi agent.

FROM python:3.13-slim

# Node 22 via NodeSource; git is used by pi for repo work.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl git ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Python deps first (layer cache).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app/ ./app/
COPY config.py pi_config.py pi_process.py session_manager.py ./
COPY static/ ./static/

# pi coding agent (provides the `pi` binary).
RUN npm install -g @earendil-works/pi-coding-agent

# Persistence is expected on a volume at /data (see Helm chart).
ENV PI_AGENT_DIR=/data/.pi-agent \
    PI_SESSION_DIR=/data/.pi-sessions \
    HARNESS_WORKSPACE=/data/workspace \
    PYTHONPATH=/app

EXPOSE 8080

# Run the venv uvicorn directly: `uv run` would re-sync (and re-install dev
# deps like ruff) at container start, needing network and adding latency.
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
