"""View state machine: a home screen of rooms, and per-room device screens.

The Stream Deck has no native folders, so navigation is modelled here:

  * HOME  — one key per room (paginated if they don't all fit).
  * ROOM  — key 0 is Back; the room's devices fill the remaining keys
            (paginated, reserving the last two keys for Prev/Next when needed).

Key presses (deck worker thread) and live state updates (event thread) both
mutate the display, so all rendering goes through a single lock.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from ..deck.renderer import KeyRenderer
from ..ha.model import DeviceEntity, Room, Status

logger = logging.getLogger(__name__)

# Sentinel area id for the virtual "Lights On" folder.
LIGHTS_ON_AREA = "__lights_on__"


class View(Enum):
    HOME = auto()
    ROOM = auto()


class ActionKind(Enum):
    OPEN_ROOM = auto()
    ENTITY = auto()
    BACK = auto()
    PAGE = auto()
    BLANK = auto()


@dataclass
class Action:
    kind: ActionKind
    room: Room | None = None
    entity: DeviceEntity | None = None
    delta: int = 0


class Display:
    """Minimal surface navigation needs from a target (deck or export)."""

    key_count: int

    def set_image(self, key: int, image) -> None: ...  # pragma: no cover


# fn(entity) -> None: perform the Home Assistant service call for a press.
ServiceCallback = Callable[[DeviceEntity], None]


class Navigation:
    def __init__(self, display: Display, renderer: KeyRenderer, rooms: list[Room], on_service: ServiceCallback) -> None:
        self.display = display
        self.renderer = renderer
        self.rooms = rooms
        self.on_service = on_service

        # Virtual folder, always first on the home screen, listing the lights
        # that are currently on across every room.
        self.lights_on_room = Room(
            area_id=LIGHTS_ON_AREA,
            name="Lights On",
            icon="mdi:lightbulb-on",
            is_dynamic=True,
        )

        self.view = View.HOME
        self.current_room: Room | None = None
        self.page = 0
        self.key_map: dict[int, Action] = {}
        self._lock = threading.RLock()
        self._disconnected = False

    # -- rendering ----------------------------------------------------------

    def render(self) -> None:
        """Rebuild the key map for the current view/page and draw every key."""
        with self._lock:
            if self._disconnected:
                self._draw_disconnected()
                return
            self.key_map = self._build_key_map()
            for key in range(self.display.key_count):
                self.display.set_image(key, self._image_for(self.key_map.get(key)))

    def _image_for(self, action: Action | None):
        if action is None or action.kind is ActionKind.BLANK:
            return self.renderer.blank()
        if action.kind is ActionKind.OPEN_ROOM:
            return self.renderer.room(action.room, dynamic=action.room.is_dynamic)
        if action.kind is ActionKind.ENTITY:
            return self.renderer.device(action.entity)
        if action.kind is ActionKind.BACK:
            return self.renderer.nav("back")
        if action.kind is ActionKind.PAGE:
            return self.renderer.nav("next" if action.delta > 0 else "prev")
        return self.renderer.blank()

    def _build_key_map(self) -> dict[int, Action]:
        total = self.display.key_count
        if self.view is View.HOME:
            # The dynamic "Lights On" folder is always listed first.
            home_rooms = [self.lights_on_room, *self.rooms]
            items = [Action(ActionKind.OPEN_ROOM, room=r) for r in home_rooms]
            fixed: dict[int, Action] = {}
        else:
            room = self.current_room
            items = [Action(ActionKind.ENTITY, entity=e) for e in (room.entities if room else [])]
            fixed = {0: Action(ActionKind.BACK)}
        return layout_page(items, total, fixed, self.page)

    # -- press handling -----------------------------------------------------

    def handle_press(self, key: int, pressed: bool) -> None:
        if not pressed:
            return  # act on the press-down edge only
        with self._lock:
            action = self.key_map.get(key)
        if action is None:
            return

        if action.kind is ActionKind.OPEN_ROOM:
            self._goto_room(action.room)
        elif action.kind is ActionKind.BACK:
            self._goto_home()
        elif action.kind is ActionKind.PAGE:
            self._change_page(action.delta)
        elif action.kind is ActionKind.ENTITY and action.entity is not None:
            if action.entity.is_controllable:
                self._invoke(action.entity)

    # Public navigation entry points (also used by the export tool).
    def open_room(self, room: Room) -> None:
        self._goto_room(room)

    def home(self) -> None:
        self._goto_home()

    def _goto_room(self, room: Room | None) -> None:
        with self._lock:
            self.view = View.ROOM
            if room is not None and room.is_dynamic:
                room.entities = self._collect_on_lights()
            self.current_room = room
            self.page = 0
        self.render()

    def _collect_on_lights(self) -> list[DeviceEntity]:
        """All light entities currently on, across every room, sorted by name."""
        lights = [
            entity
            for room in self.rooms
            for entity in room.entities
            if entity.domain == "light" and entity.status is Status.ON
        ]
        lights.sort(key=lambda e: e.name.lower())
        return lights

    def _goto_home(self) -> None:
        with self._lock:
            self.view = View.HOME
            self.current_room = None
            self.page = 0
        self.render()

    def _change_page(self, delta: int) -> None:
        with self._lock:
            self.page = max(0, self.page + delta)
        self.render()

    def _invoke(self, entity: DeviceEntity) -> None:
        try:
            self.on_service(entity)
        except Exception as exc:  # noqa: BLE001 - a bad call must not kill the deck thread
            logger.warning("Service call for %s failed: %s", entity.entity_id, exc)

    # -- live updates -------------------------------------------------------

    def refresh_entity(self, entity_id: str) -> None:
        """Re-render the key showing ``entity_id``, if it is on screen.

        While viewing the dynamic "Lights On" folder, a light toggling changes
        which lights belong there, so the whole view is rebuilt instead.
        """
        with self._lock:
            if self._disconnected:
                return
            viewing_dynamic = (
                self.view is View.ROOM
                and self.current_room is not None
                and self.current_room.is_dynamic
                and entity_id.startswith("light.")  # only lights change membership
            )
        if viewing_dynamic:
            self._goto_room(self.current_room)  # recompute membership + redraw
            return
        with self._lock:
            for key, action in self.key_map.items():
                if action.kind is ActionKind.ENTITY and action.entity and action.entity.entity_id == entity_id:
                    self.display.set_image(key, self.renderer.device(action.entity))
                    return

    def set_connected(self, connected: bool) -> None:
        with self._lock:
            was_disconnected = self._disconnected
            self._disconnected = not connected
        if connected and was_disconnected:
            self.render()  # restore the current view
        elif not connected:
            with self._lock:
                self._draw_disconnected()

    def _draw_disconnected(self) -> None:
        img = self.renderer.message("No HA")
        for key in range(self.display.key_count):
            self.display.set_image(key, img)


def layout_page(items: list[Action], total_keys: int, fixed: dict[int, Action], page: int) -> dict[int, Action]:
    """Place ``items`` onto a page of keys, adding Prev/Next when they overflow.

    ``fixed`` keys (e.g. Back at 0) are always reserved. If items fit on one
    page, no pagination keys are added; otherwise the last two free keys become
    Prev (second-to-last) and Next (last), and the remaining free keys hold the
    page's slice. Returns {key_index: Action}; keys not present are blank.
    """
    free = [k for k in range(total_keys) if k not in fixed]
    result: dict[int, Action] = dict(fixed)

    if len(items) <= len(free):
        for slot, action in zip(free, items):
            result[slot] = action
        return result

    next_key = free[-1]
    prev_key = free[-2]
    slots = free[:-2]
    per_page = len(slots)
    page_count = (len(items) + per_page - 1) // per_page
    page = max(0, min(page, page_count - 1))

    start = page * per_page
    for slot, action in zip(slots, items[start : start + per_page]):
        result[slot] = action

    if page > 0:
        result[prev_key] = Action(ActionKind.PAGE, delta=-1)
    if page < page_count - 1:
        result[next_key] = Action(ActionKind.PAGE, delta=1)
    return result
