"""Material Design Icons lookup and per-domain default icon selection.

Home Assistant entities only carry an explicit ``icon`` attribute when the user
sets one; otherwise the frontend derives an icon from the domain/device_class.
We mirror that: prefer the entity's own icon, then a domain/device_class
default, then a generic fallback — always resolving to an icon that actually
exists in the bundled MDI font.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
META_PATH = ASSETS_DIR / "mdi-meta.json"

GENERIC_FALLBACK = "help-circle"

# Closure device_class -> (open icon, closed icon), chosen by current state.
CLOSURE_ICONS: dict[str, tuple[str, str]] = {
    "door": ("door-open", "door"),
    "garage_door": ("garage-open", "garage"),
    "garage": ("garage-open", "garage"),
    "gate": ("gate-open", "gate"),
    "window": ("window-open-variant", "window-closed-variant"),
}

# Default icon per domain when the entity has no explicit icon.
DOMAIN_ICONS: dict[str, str] = {
    "light": "lightbulb",
    "switch": "toggle-switch-variant",
    "input_boolean": "toggle-switch-variant",
    "fan": "fan",
    "cover": "window-shutter",
    "climate": "thermostat",
    "sensor": "eye",
    "binary_sensor": "checkbox-blank-circle",
    "button": "gesture-tap-button",
    "input_button": "gesture-tap-button",
    "timer": "timer-outline",
    "media_player": "cast",
    "alarm_control_panel": "shield-home-outline",
}

# More specific defaults keyed by (domain, device_class).
DEVICE_CLASS_ICONS: dict[tuple[str, str], str] = {
    ("sensor", "temperature"): "thermometer",
    ("sensor", "humidity"): "water-percent",
    ("sensor", "power"): "flash",
    ("sensor", "energy"): "lightning-bolt",
    ("sensor", "battery"): "battery",
    ("sensor", "illuminance"): "brightness-5",
    ("sensor", "pressure"): "gauge",
    ("sensor", "voltage"): "sine-wave",
    ("sensor", "current"): "current-ac",
    ("sensor", "carbon_dioxide"): "molecule-co2",
    ("sensor", "distance"): "ruler",
    ("sensor", "signal_strength"): "wifi",
    ("sensor", "timestamp"): "clock-outline",
    ("sensor", "date"): "calendar",
    ("binary_sensor", "motion"): "motion-sensor",
    ("binary_sensor", "door"): "door",
    ("binary_sensor", "window"): "window-closed-variant",
    ("binary_sensor", "moisture"): "water",
    ("binary_sensor", "smoke"): "smoke-detector",
    ("binary_sensor", "occupancy"): "account",
    ("binary_sensor", "presence"): "home-account",
    ("binary_sensor", "moving"): "walk",
    ("binary_sensor", "opening"): "square-outline",
    ("button", "restart"): "restart",
    ("button", "update"): "package-up",
    ("button", "identify"): "crosshairs-question",
    ("cover", "garage"): "garage",
    ("cover", "shade"): "roller-shade",
    ("cover", "curtain"): "curtains",
    ("cover", "blind"): "blinds",
    # Match Home Assistant's media_player icons by device class.
    ("media_player", "tv"): "television",
    ("media_player", "speaker"): "speaker",
    ("media_player", "receiver"): "audio-video",
}


@lru_cache(maxsize=1)
def _codepoints() -> dict[str, int]:
    """Map MDI icon name -> integer codepoint, loaded once from meta.json."""
    if not META_PATH.exists():
        raise FileNotFoundError(
            f"{META_PATH} not found. Run `python scripts/fetch_assets.py` first."
        )
    data = json.loads(META_PATH.read_text())
    return {entry["name"]: int(entry["codepoint"], 16) for entry in data}


def font_path() -> Path:
    path = ASSETS_DIR / "materialdesignicons-webfont.ttf"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python scripts/fetch_assets.py` first."
        )
    return path


def _normalize(name: str | None) -> str | None:
    """Strip an optional ``mdi:`` prefix; return None for empties."""
    if not name:
        return None
    name = name.strip()
    if name.startswith("mdi:"):
        name = name[len("mdi:") :]
    return name or None


def resolve_icon_name(
    domain: str,
    device_class: str | None,
    explicit: str | None,
    state: str | None = None,
    is_open: bool | None = None,
) -> str:
    """Pick the best MDI icon name that exists in the font.

    Order: explicit entity icon -> state-aware default (locks, open/closed
    closures) -> (domain, device_class) -> domain -> generic.
    """
    codepoints = _codepoints()

    explicit_name = _normalize(explicit)
    if explicit_name and explicit_name in codepoints:
        return explicit_name

    if is_open is not None and device_class in CLOSURE_ICONS:
        open_name, closed_name = CLOSURE_ICONS[device_class]
        name = open_name if is_open else closed_name
        if name in codepoints:
            return name

    if domain == "timer":
        s = (state or "").lower()
        name = "timer-play-outline" if s == "active" else "timer-pause-outline" if s == "paused" else "timer-outline"
        if name in codepoints:
            return name

    if domain == "alarm_control_panel":
        name = {
            "disarmed": "shield-off-outline",
            "armed_home": "shield-home",
            "armed_away": "shield-lock",
            "armed_night": "shield-moon",
            "armed_vacation": "shield-airplane",
            "armed_custom_bypass": "shield-half-full",
            "arming": "shield-sync",
            "pending": "shield-sync",
            "triggered": "shield-alert",
        }.get((state or "").lower(), "shield-home-outline")
        if name in codepoints:
            return name

    if domain == "lock":
        s = (state or "").lower()
        if s == "jammed":
            name = "lock-alert"
        elif s in ("locking", "unlocking", "opening"):
            name = "lock-clock"  # change in progress
        elif s == "locked":
            name = "lock"
        else:  # unlocked / open / unknown
            name = "lock-open-variant"
        if name in codepoints:
            return name

    if device_class:
        dc_icon = DEVICE_CLASS_ICONS.get((domain, device_class))
        if dc_icon and dc_icon in codepoints:
            return dc_icon

    domain_icon = DOMAIN_ICONS.get(domain)
    if domain_icon and domain_icon in codepoints:
        return domain_icon

    return GENERIC_FALLBACK


def glyph(icon_name: str) -> str:
    """Return the single character that renders ``icon_name`` in the MDI font."""
    codepoints = _codepoints()
    cp = codepoints.get(icon_name)
    if cp is None:
        cp = codepoints.get(GENERIC_FALLBACK)
    return chr(cp)
