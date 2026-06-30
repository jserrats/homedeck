"""Configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    brightness: int

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment, loading a .env file if present.

        Secrets never live in committed config; they come from env vars (or a
        local .env / Docker secrets). Raises ValueError with an actionable
        message when something required is missing.
        """
        load_dotenv()  # no-op if there is no .env file

        ha_url = os.environ.get("HA_URL", "").strip()
        ha_token = os.environ.get("HA_TOKEN", "").strip()

        missing = [name for name, val in (("HA_URL", ha_url), ("HA_TOKEN", ha_token)) if not val]
        if missing:
            raise ValueError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            )

        if not ha_url.startswith(("ws://", "wss://")):
            raise ValueError(
                f"HA_URL must be a websocket URL starting with ws:// or wss:// "
                f"(e.g. ws://homeassistant.local:8123/api/websocket), got: {ha_url}"
            )

        brightness = _clamp_int(os.environ.get("HOMEDECK_BRIGHTNESS"), default=60, lo=0, hi=100)
        return cls(ha_url=ha_url, ha_token=ha_token, brightness=brightness)


def _clamp_int(raw: str | None, *, default: int, lo: int, hi: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default
