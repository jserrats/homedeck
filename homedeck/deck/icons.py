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
    ("binary_sensor", "motion"): "motion-sensor",
    ("binary_sensor", "door"): "door",
    ("binary_sensor", "window"): "window-closed-variant",
    ("binary_sensor", "moisture"): "water",
    ("binary_sensor", "smoke"): "smoke-detector",
    ("binary_sensor", "occupancy"): "account",
    ("binary_sensor", "opening"): "square-outline",
    ("cover", "garage"): "garage",
    ("cover", "shade"): "roller-shade",
    ("cover", "curtain"): "curtains",
    ("cover", "blind"): "blinds",
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


def resolve_icon_name(domain: str, device_class: str | None, explicit: str | None) -> str:
    """Pick the best MDI icon name that exists in the font.

    Order: explicit entity icon -> (domain, device_class) -> domain -> generic.
    """
    codepoints = _codepoints()

    explicit_name = _normalize(explicit)
    if explicit_name and explicit_name in codepoints:
        return explicit_name

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
