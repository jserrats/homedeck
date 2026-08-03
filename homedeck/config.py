"""Configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from .ha.calendar import AGENDA_DAYS


@dataclass(frozen=True)
class Config:
    ha_url: str
    ha_token: str
    brightness: int
    weather_entity: str | None
    occupancy_entity: str | None
    timezone: str | None
    rotation: int
    agenda_days: int

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
        weather_entity = (os.environ.get("HOMEDECK_WEATHER_ENTITY") or "").strip() or None
        # Optional occupancy/presence entity: when set, the deck's display follows
        # it (on when occupied, off when clear).
        occupancy_entity = (os.environ.get("HOMEDECK_OCCUPANCY_ENTITY") or "").strip() or None
        # Fallback timezone if HA's own time_zone can't be read; TZ also sets the
        # container's local time. Defaults to Europe/Madrid.
        timezone = (os.environ.get("HOMEDECK_TZ") or os.environ.get("TZ") or "Europe/Madrid").strip()
        rotation = _clamp_int(os.environ.get("HOMEDECK_ROTATION"), default=0, lo=0, hi=270)
        rotation = (rotation // 90) * 90  # normalize to 0/90/180/270
        # How far ahead the calendar agenda looks. Every day in the window gets a
        # column, so a longer horizon means more pages to step through.
        agenda_days = _clamp_int(os.environ.get("HOMEDECK_AGENDA_DAYS"),
                                 default=AGENDA_DAYS, lo=1, hi=60)
        return cls(
            ha_url=ha_url, ha_token=ha_token, brightness=brightness,
            weather_entity=weather_entity, occupancy_entity=occupancy_entity,
            timezone=timezone, rotation=rotation, agenda_days=agenda_days,
        )


def _clamp_int(raw: str | None, *, default: int, lo: int, hi: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        return max(lo, min(hi, int(raw)))
    except ValueError:
        return default
