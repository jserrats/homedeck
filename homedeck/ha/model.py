"""Domain model: rooms (HA areas) populated with their entities.

This module is pure data + logic (no I/O, no rendering, no Stream Deck), so it
is straightforward to unit test. It turns the raw registry/state dictionaries
returned by the WebSocket API into ``Room`` and ``DeviceEntity`` objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# Domains we surface on the deck.
TOGGLE_DOMAINS = frozenset({"light", "switch", "input_boolean", "fan", "cover"})
DISPLAY_DOMAINS = frozenset({"sensor", "binary_sensor", "climate"})
IN_SCOPE_DOMAINS = TOGGLE_DOMAINS | DISPLAY_DOMAINS

# Entity categories that HA tucks away (not shown as primary controls in the UI).
HIDDEN_ENTITY_CATEGORIES = frozenset({"config", "diagnostic"})

# States that mean "the entity is currently active/on".
ON_STATES = frozenset({"on", "open", "opening", "home", "playing", "heat", "cool", "auto"})
OFF_STATES = frozenset({"off", "closed", "closing", "not_home", "idle", "standby"})
UNAVAILABLE_STATES = frozenset({"unavailable", "unknown", "none", ""})


class Status(Enum):
    """Coarse status used to pick a key color."""

    ON = "on"
    OFF = "off"
    UNAVAILABLE = "unavailable"


def domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


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
        return self.domain in TOGGLE_DOMAINS

    @property
    def status(self) -> Status:
        state = (self.state or "").lower()
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
        entities show their current reading.
        """
        if self.domain == "sensor":
            unit = self.attributes.get("unit_of_measurement", "")
            if self.status is Status.UNAVAILABLE:
                return "—"
            return f"{self.state}{unit}"
        if self.domain == "climate":
            current = self.attributes.get("current_temperature")
            if current is not None:
                return f"{current}°"
            return self.state
        return None

    def service_call(self) -> tuple[str, str, str] | None:
        """Return (domain, service, entity_id) to call on press, or None.

        Lights/switches/fans/covers toggle; everything else is display-only.
        """
        if self.domain in TOGGLE_DOMAINS:
            return (self.domain, "toggle", self.entity_id)
        return None

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
    entities: list[DeviceEntity] = field(default_factory=list)


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
