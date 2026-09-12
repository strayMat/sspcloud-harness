"""Seed the isolated pi agent dir so the harness runs without touching
the developer's real ~/.pi/agent.
"""

from __future__ import annotations

import json

import config


def seed_agent_dir() -> None:
    """Write models.json + settings.json into the isolated AGENT_DIR.

    Idempotent: only writes a file if it doesn't already exist, so local
    tweaks to the config survive restarts.
    """
    config.ensure_layout()
    config.AGENT_DIR.mkdir(parents=True, exist_ok=True)

    models = {
        "providers": {
            config.PROVIDER: {
                "baseUrl": "https://llm.lab.sspcloud.fr/api/v1",
                "api": "openai-completions",
                # Env interpolation: pi resolves $SSP_LLM_KEY from the
                # subprocess environment at request time.
                "apiKey": f"${config.LLM_KEY_ENV}",
                "models": [{"id": config.MODEL_ID}],
            }
        }
    }
    models_path = config.AGENT_DIR / "models.json"
    if not models_path.exists():
        models_path.write_text(json.dumps(models, indent=2))

    settings = {
        "defaultProvider": config.PROVIDER,
        "defaultModel": config.MODEL_ID,
        # No packages locally: pi-mcp-adapter / pi-web-access are wired at
        # deploy time (they need the sspcloud-mcp service to be reachable).
        "theme": "dark",
    }
    settings_path = config.AGENT_DIR / "settings.json"
    if not settings_path.exists():
        settings_path.write_text(json.dumps(settings, indent=2))
