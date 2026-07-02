"""Domain model: rooms (HA areas) populated with their entities.

This module is pure data + logic (no I/O, no rendering, no Stream Deck), so it
is straightforward to unit test. It turns the raw registry/state dictionaries
returned by the WebSocket API into ``Room`` and ``DeviceEntity`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..color import hs_to_rgb, kelvin_to_rgb, scale

# Domains we surface on the deck.
TOGGLE_DOMAINS = frozenset({"light", "switch", "input_boolean", "fan", "cover"})
LOCK_DOMAIN = "lock"  # state-based control + long-press to open
BUTTON_DOMAINS = frozenset({"button", "input_button"})  # momentary press (.press)

# Door/window/closure device classes (binary_sensor + door-like covers) that
# should read green when closed and orange when open.
CLOSURE_DEVICE_CLASSES = frozenset({"door", "garage_door", "garage", "gate", "window", "opening"})

# binary_sensor device classes that represent presence/motion.
PRESENCE_DEVICE_CLASSES = frozenset({"motion", "occupancy", "presence", "moving"})

# HA light color modes that carry an actual RGB/HS color (vs color_temp/brightness).
LIGHT_COLOR_MODES = frozenset({"hs", "xy", "rgb", "rgbw", "rgbww", "rgbwww"})

# Warm-white fallback tint for a brightness-only light (no color/temp info).
WARM_WHITE = (255, 210, 160)

# On/off devices that get a clear "off" bar when off (a dim colored light can
# otherwise look like it's on). Covers/locks have their own open/closed colors.
OFF_INDICATOR_DOMAINS = frozenset({"light", "switch", "fan", "input_boolean"})

# Climate-related domains whose icon reads sky-blue (not the amber "on") when active.
CLIMATE_DOMAINS = frozenset({"fan", "climate"})

# Domains whose long-press opens a state-history / logbook view.
HISTORY_DOMAINS = frozenset({"switch", "binary_sensor"})
DISPLAY_DOMAINS = frozenset({"sensor", "binary_sensor", "climate"})
CONTROLLABLE_DOMAINS = TOGGLE_DOMAINS | {LOCK_DOMAIN} | BUTTON_DOMAINS
IN_SCOPE_DOMAINS = CONTROLLABLE_DOMAINS | DISPLAY_DOMAINS

# Entity categories that HA tucks away (not shown as primary controls in the UI).
HIDDEN_ENTITY_CATEGORIES = frozenset({"config", "diagnostic"})

# States that mean "the entity is currently active/on".
ON_STATES = frozenset({"on", "open", "opening", "home", "playing", "heat", "cool", "auto"})
OFF_STATES = frozenset({"off", "closed", "closing", "not_home", "idle", "standby"})
UNAVAILABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})


class Status(Enum):
    """Visual status used to pick a key color."""

    ON = "on"                    # lights/switches/etc. active (accent)
    OFF = "off"                  # inactive / neutral
    UNAVAILABLE = "unavailable"  # unavailable or error (e.g. a jammed lock)
    SECURE = "secure"            # locked, or a closed door/window (green)
    OPEN = "open"                # an open door/window/closure (orange)
    PENDING = "pending"          # a transitional state, e.g. locking/unlocking/opening


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


# Most decimal places to show on a key; HA's own display precision isn't in the
# state, so we just trim float noise (e.g. 78.40000000001 -> 78.4).
MAX_DECIMALS = 2


def _format_number(raw: object) -> str:
    """Render a numeric state cleanly; pass non-numeric states through.

    Integers stay integers; floats are rounded to ``MAX_DECIMALS`` with trailing
    zeros stripped. This removes the long binary-float tails HA sometimes emits.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if value.is_integer():
        return str(int(value))
    text = f"{round(value, MAX_DECIMALS):.{MAX_DECIMALS}f}".rstrip("0").rstrip(".")
    return text


def _with_unit(value: str, unit: str) -> str:
    """Join value and unit; space-separated except for percent, like the HA UI."""
    if not unit:
        return value
    if unit == "%":
        return f"{value}{unit}"
    return f"{value} {unit}"


@dataclass
class DeviceEntity:
    entity_id: str
    name: str
    domain: str
    state: str
    attributes: dict = field(default_factory=dict)
    device_class: str | None = None

    @property
    def is_controllable(self) -> bool:
        return self.domain in CONTROLLABLE_DOMAINS

    @property
    def is_off(self) -> bool:
        """An on/off device (light/switch/fan/input_boolean) that is currently off."""
        return self.domain in OFF_INDICATOR_DOMAINS and self.status is Status.OFF

    @property
    def is_closure(self) -> bool:
        """A door/window/closure entity (binary_sensor or door-like cover)."""
        return self.domain in ("binary_sensor", "cover") and self.device_class in CLOSURE_DEVICE_CLASSES

    @property
    def is_presence(self) -> bool:
        """A motion/occupancy/presence binary_sensor."""
        return self.domain == "binary_sensor" and self.device_class in PRESENCE_DEVICE_CLASSES

    def closure_open(self) -> bool | None:
        """For closures: True if open, False if closed, None if unknown/transitional.

        HA convention: a closure binary_sensor reads ``on`` = open, ``off`` =
        closed; a cover reads ``open``/``closed``.
        """
        if not self.is_closure:
            return None
        state = (self.state or "").lower()
        if state in UNAVAILABLE_STATES:
            return None
        if self.domain == "cover":
            if state == "closed":
                return False
            if state in ("opening", "closing"):
                return None
            return True  # open
        # binary_sensor
        if state == "on":
            return True
        if state == "off":
            return False
        return None

    @property
    def status(self) -> Status:
        state = (self.state or "").lower()
        if self.domain in BUTTON_DOMAINS:
            # Stateless: always actionable. Its state is a last-pressed timestamp
            # (or "unknown" before the first press), so only flag real outages.
            return Status.UNAVAILABLE if state == "unavailable" else Status.ON
        if self.domain == LOCK_DOMAIN:
            # Locked = secure (green); in transition = pending; jammed = alert;
            # unlocked/open = neutral.
            if state in UNAVAILABLE_STATES or state == "jammed":
                return Status.UNAVAILABLE
            if state in ("locking", "unlocking", "opening"):
                return Status.PENDING
            if state == "locked":
                return Status.SECURE
            return Status.OFF
        if self.domain == "cover":
            # All covers: closed = green, open = orange, moving = pending.
            if state in UNAVAILABLE_STATES:
                return Status.UNAVAILABLE
            if state in ("opening", "closing"):
                return Status.PENDING
            return Status.SECURE if state == "closed" else Status.OPEN
        if self.is_closure:
            # Doors/windows/closures: closed = secure (green), open = orange.
            if state in UNAVAILABLE_STATES:
                return Status.UNAVAILABLE
            if self.domain == "cover" and state in ("opening", "closing"):
                return Status.PENDING
            opened = self.closure_open()
            if opened is True:
                return Status.OPEN
            if opened is False:
                return Status.SECURE
            return Status.OFF
        if state in UNAVAILABLE_STATES:
            return Status.UNAVAILABLE
        if state in OFF_STATES:
            return Status.OFF
        if state in ON_STATES:
            return Status.ON
        # Numeric/text sensors: treat as informational ("off" palette = neutral).
        return Status.OFF

    @property
    def explicit_icon(self) -> str | None:
        return self.attributes.get("icon")

    def display_value(self) -> str | None:
        """The text shown as the key's main value (sensors/climate), else None.

        Controllable devices show only their name + colored icon; read-only
        entities show their current reading, with numbers cleaned up (float
        noise stripped) and the unit spaced like the HA UI (e.g. "78.4 cm").
        """
        if self.domain == "sensor":
            if self.status is Status.UNAVAILABLE:
                return "—"
            unit = self.attributes.get("unit_of_measurement", "")
            return _with_unit(_format_number(self.state), unit)
        if self.domain == "climate":
            current = self.attributes.get("current_temperature")
            if current is not None:
                return f"{_format_number(current)}°"
            return self.state
        return None

    def service_call(self) -> tuple[str, str, str, dict] | None:
        """Return (domain, service, entity_id, service_data) for a single press.

        Lights/switches/fans/covers toggle; a lock locks or unlocks depending on
        its current state; sensors/climate are display-only (None).
        """
        if self.domain in TOGGLE_DOMAINS:
            return (self.domain, "toggle", self.entity_id, {})
        if self.domain == LOCK_DOMAIN:
            service = "unlock" if (self.state or "").lower() == "locked" else "lock"
            return (LOCK_DOMAIN, service, self.entity_id, {})
        if self.domain in BUTTON_DOMAINS:
            return (self.domain, "press", self.entity_id, {})
        return None

    def long_press_call(self) -> tuple[str, str, str, dict] | None:
        """Service to run on a long press, or None (e.g. the light grid is not a
        plain service call). Locks open the door/latch (lock.open)."""
        if self.domain == LOCK_DOMAIN:
            return (LOCK_DOMAIN, "open", self.entity_id, {})
        return None

    @property
    def supports_dynamic_color(self) -> bool:
        """A light that can dim or change color/temperature (not plain on/off)."""
        if self.domain != "light":
            return False
        modes = set(self.attributes.get("supported_color_modes") or [])
        return bool(modes - {"onoff", None})

    def icon_color(self) -> tuple[int, int, int] | None:
        """The icon tint reflecting the light's current color/temperature/brightness.

        Returns None to fall back to the status palette — for non-lights, plain
        on/off lights, or lights that are off/unavailable.
        """
        if not self.supports_dynamic_color or self.status is not Status.ON:
            return None

        attrs = self.attributes
        mode = attrs.get("color_mode")
        rgb, hs = attrs.get("rgb_color"), attrs.get("hs_color")
        kelvin, mireds = attrs.get("color_temp_kelvin"), attrs.get("color_temp")

        base: tuple[int, int, int] | None = None
        if mode in LIGHT_COLOR_MODES or (mode is None and (rgb or hs)):
            if rgb:
                base = (int(rgb[0]), int(rgb[1]), int(rgb[2]))
            elif hs:
                base = hs_to_rgb(float(hs[0]), float(hs[1]))
        elif mode == "color_temp" or (mode is None and (kelvin or mireds)):
            if kelvin:
                base = kelvin_to_rgb(int(kelvin))
            elif mireds:
                base = kelvin_to_rgb(round(1_000_000 / int(mireds)))
        if base is None:
            base = WARM_WHITE  # brightness-only, or color info missing

        brightness = attrs.get("brightness")
        if brightness is not None:
            # Keep a floor so dim lights stay visible on the dark key.
            base = scale(base, max(0.45, int(brightness) / 255))
        return base

    @property
    def supports_light_grid(self) -> bool:
        """A light that supports both dimming and color temperature.

        HA's ``color_temp`` color mode implies brightness support, so its
        presence in ``supported_color_modes`` is enough.
        """
        if self.domain != "light":
            return False
        modes = self.attributes.get("supported_color_modes") or []
        return "color_temp" in modes

    @property
    def supports_rgb_color(self) -> bool:
        """A light that supports full RGB/HS color."""
        if self.domain != "light":
            return False
        modes = set(self.attributes.get("supported_color_modes") or [])
        return bool(modes & LIGHT_COLOR_MODES)

    @staticmethod
    def _brightness_levels(n: int) -> list[int]:
        low = 10
        if n <= 1:
            return [100]
        return [round(low + (100 - low) * i / (n - 1)) for i in range(n)]

    def light_grid_levels(self, n_brightness: int = 4, n_color: int = 8) -> tuple[list[int], list[int]]:
        """Return (brightness_percents, color_temp_kelvins), low→high.

        Brightness spans 10→100%; color temperature spans the light's own
        ``min_color_temp_kelvin``→``max_color_temp_kelvin`` (its full range).
        """
        brightness = self._brightness_levels(n_brightness)
        min_k = int(self.attributes.get("min_color_temp_kelvin") or 2000)
        max_k = int(self.attributes.get("max_color_temp_kelvin") or 6500)
        if n_color <= 1:
            kelvins = [min_k]
        else:
            kelvins = [round(min_k + (max_k - min_k) * j / (n_color - 1)) for j in range(n_color)]
        return brightness, kelvins

    def color_grid_levels(self, n_brightness: int = 4, n_color: int = 8) -> tuple[list[int], list[int]]:
        """Return (brightness_percents, hues), for the RGB picker.

        Hues are spread evenly around the color wheel (0→360°).
        """
        brightness = self._brightness_levels(n_brightness)
        hues = [round(j * 360 / n_color) for j in range(n_color)]
        return brightness, hues

    @property
    def supports_history(self) -> bool:
        """Switches and binary sensors open a logbook/history view on long-press."""
        return self.domain in HISTORY_DOMAINS

    @property
    def has_long_press(self) -> bool:
        return (
            self.long_press_call() is not None
            or self.supports_light_grid
            or self.supports_rgb_color
            or self.supports_history
        )

    def update_from_state(self, state: str, attributes: dict | None) -> None:
        self.state = state
        if attributes is not None:
            self.attributes = attributes
            self.device_class = attributes.get("device_class", self.device_class)


@dataclass
class Room:
    area_id: str
    name: str
    icon: str | None = None  # explicit area icon from HA, e.g. "mdi:sofa"
    floor_id: str | None = None  # HA floor this area belongs to, if any
    entities: list[DeviceEntity] = field(default_factory=list)
    is_dynamic: bool = False  # virtual folder whose contents are recomputed live


@dataclass
class Floor:
    floor_id: str
    name: str
    level: int = 0  # HA floor level; used for ordering (ground = 0)
    icon: str | None = None
    rooms: list[Room] = field(default_factory=list)


def _entity_friendly_name(entity_id: str, reg_name: str | None, state_attrs: dict) -> str:
    """Best available human name: registry override -> friendly_name -> id slug."""
    if reg_name:
        return reg_name
    fn = state_attrs.get("friendly_name")
    if fn:
        return fn
    return entity_id.split(".", 1)[-1].replace("_", " ").title()


def resolve_area_id(entity_entry: dict, devices_by_id: dict[str, dict]) -> str | None:
    """Effective area for an entity: its own area_id, else its device's area_id."""
    area_id = entity_entry.get("area_id")
    if area_id:
        return area_id
    device_id = entity_entry.get("device_id")
    if device_id:
        device = devices_by_id.get(device_id)
        if device:
            return device.get("area_id")
    return None


def build_rooms(
    areas: list[dict],
    entity_entries: list[dict],
    device_entries: list[dict],
    states_by_id: dict[str, dict],
) -> list[Room]:
    """Assemble rooms from HA registries + current states.

    - Skips hidden/disabled entities, diagnostic/config entities, and
      out-of-scope domains, to mirror what the Home Assistant UI shows.
    - Resolves each entity's area (with device fallback).
    - Sorts rooms alphabetically and entities by name within each room.

    ``states_by_id`` maps entity_id -> {"state": str, "attributes": dict}.
    """
    devices_by_id = {d["id"]: d for d in device_entries if d.get("id")}
    rooms_by_area: dict[str, Room] = {
        a["area_id"]: Room(
            area_id=a["area_id"],
            name=a.get("name") or a["area_id"],
            icon=a.get("icon"),
            floor_id=a.get("floor_id"),
        )
        for a in areas
        if a.get("area_id")
    }

    for entry in entity_entries:
        entity_id = entry.get("entity_id")
        if not entity_id:
            continue
        if entry.get("hidden_by") or entry.get("disabled_by"):
            continue
        if entry.get("entity_category") in HIDDEN_ENTITY_CATEGORIES:
            continue
        domain = domain_of(entity_id)
        if domain not in IN_SCOPE_DOMAINS:
            continue

        area_id = resolve_area_id(entry, devices_by_id)
        room = rooms_by_area.get(area_id) if area_id else None
        if room is None:
            continue

        state_info = states_by_id.get(entity_id, {})
        attrs = state_info.get("attributes", {}) or {}
        device = DeviceEntity(
            entity_id=entity_id,
            name=_entity_friendly_name(entity_id, entry.get("name"), attrs),
            domain=domain,
            state=state_info.get("state", "unavailable"),
            attributes=attrs,
            device_class=attrs.get("device_class") or entry.get("device_class"),
        )
        room.entities.append(device)

    rooms = [r for r in rooms_by_area.values() if r.entities]
    rooms.sort(key=lambda r: r.name.lower())
    for room in rooms:
        room.entities.sort(key=lambda e: e.name.lower())
    return rooms


def group_by_floor(floor_entries: list[dict], rooms: list[Room]) -> tuple[list[Floor], list[Room]]:
    """Distribute rooms into their HA floors.

    Returns ``(floors, unassigned)`` where ``floors`` only includes floors that
    actually contain rooms, ordered by HA floor level then name, and
    ``unassigned`` holds rooms with no (known) floor, ordered by name. When the
    registry is empty (older HA, or no floors configured) ``floors`` is empty
    and every room ends up in ``unassigned`` — callers fall back to a flat list.
    """
    floors_by_id = {
        f["floor_id"]: Floor(
            floor_id=f["floor_id"],
            name=f.get("name") or f["floor_id"],
            level=f.get("level") or 0,
            icon=f.get("icon"),
        )
        for f in floor_entries
        if f.get("floor_id")
    }

    unassigned: list[Room] = []
    for room in rooms:
        floor = floors_by_id.get(room.floor_id) if room.floor_id else None
        if floor is not None:
            floor.rooms.append(room)
        else:
            unassigned.append(room)

    floors = [f for f in floors_by_id.values() if f.rooms]
    floors.sort(key=lambda f: (f.level, f.name.lower()))
    for floor in floors:
        floor.rooms.sort(key=lambda r: r.name.lower())
    unassigned.sort(key=lambda r: r.name.lower())
    return floors, unassigned
