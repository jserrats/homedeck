"""View state machine for navigating rooms, grouped by floor.

The Stream Deck has no native folders, so navigation is modelled here as a stack
of frames:

  * HOME  — the "Lights On" folder, then all room folders. When HA has floors,
            the rooms are grouped on the same screen behind a non-interactive
            floor-header tile per floor (no extra level to drill into).
  * ROOM  — the room's devices.

The room frame reserves key 0 for Back (pop the stack). Frames paginate,
reserving the last two keys for Prev/Next when their items overflow.

Key presses (deck worker thread) and live state updates (event thread) both
mutate the display, so all rendering goes through a single lock.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from ..deck.renderer import KeyRenderer
from ..ha.model import DeviceEntity, Floor, Room, Status

logger = logging.getLogger(__name__)

# Sentinel area id for the virtual "Lights On" folder.
LIGHTS_ON_AREA = "__lights_on__"

# Hold at least this long for a press to count as a long press (e.g. open a lock).
LONG_PRESS_S = 0.5


class FrameKind(Enum):
    HOME = auto()
    ROOM = auto()


@dataclass
class Frame:
    kind: FrameKind
    room: Room | None = None
    page: int = 0


class ActionKind(Enum):
    OPEN_ROOM = auto()
    FLOOR_HEADER = auto()  # non-interactive section label
    ENTITY = auto()
    BACK = auto()
    PAGE = auto()
    BLANK = auto()


@dataclass
class Action:
    kind: ActionKind
    floor: Floor | None = None
    room: Room | None = None
    entity: DeviceEntity | None = None
    delta: int = 0


class Display:
    """Minimal surface navigation needs from a target (deck or export)."""

    key_count: int

    def set_image(self, key: int, image) -> None: ...  # pragma: no cover


# fn((domain, service, entity_id)) -> None: execute a Home Assistant service call.
ServiceCallback = Callable[[tuple[str, str, str]], None]


class Navigation:
    def __init__(
        self,
        display: Display,
        renderer: KeyRenderer,
        rooms: list[Room],
        on_service: ServiceCallback,
        floors: list[Floor] | None = None,
        unassigned_rooms: list[Room] | None = None,
    ) -> None:
        self.display = display
        self.renderer = renderer
        self.rooms = rooms
        self.on_service = on_service
        # When floors exist, the home screen lists floor folders (+ unassigned
        # rooms); otherwise it lists rooms directly.
        self.floors = floors or []
        self.unassigned_rooms = unassigned_rooms or []

        # Virtual folder, always first on the home screen, listing the lights
        # that are currently on across every room.
        self.lights_on_room = Room(
            area_id=LIGHTS_ON_AREA,
            name="Lights On",
            icon="mdi:lightbulb-on",
            is_dynamic=True,
        )

        self.stack: list[Frame] = [Frame(FrameKind.HOME)]
        self.key_map: dict[int, Action] = {}
        self._press_start: dict[int, float] = {}  # key -> press-down time, for long-press keys
        self._hold_timers: dict[int, threading.Timer] = {}  # key -> armed-feedback timer
        self._lock = threading.RLock()
        self._disconnected = False

    # -- rendering ----------------------------------------------------------

    def render(self) -> None:
        """Rebuild the key map for the current frame/page and draw every key."""
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
        if action.kind is ActionKind.FLOOR_HEADER:
            return self.renderer.floor_header(action.floor)
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
        frame = self.stack[-1]
        if frame.kind is FrameKind.ROOM:
            return self._room_key_map(frame)
        items = self._items_for(frame)
        # Any frame below the home frame gets a Back key.
        fixed: dict[int, Action] = {0: Action(ActionKind.BACK)} if len(self.stack) > 1 else {}
        return layout_page(items, self.display.key_count, fixed, frame.page)

    def _room_key_map(self, frame: Frame) -> dict[int, Action]:
        """Room view: controls in the top rows, sensors in a bottom band."""
        room = frame.room
        if room is not None and room.is_dynamic:
            room.entities = self._collect_on_lights()  # recompute live membership
        entities = room.entities if room else []
        controls = [Action(ActionKind.ENTITY, entity=e) for e in entities if e.is_controllable]
        readouts = [Action(ActionKind.ENTITY, entity=e) for e in entities if not e.is_controllable]

        cols = getattr(self.display, "cols", 0)
        if not cols:  # no grid info: fall back to a flat sequential layout
            return layout_page(controls + readouts, self.display.key_count, {0: Action(ActionKind.BACK)}, frame.page)
        return layout_room(controls, readouts, self.display.key_count, cols, frame.page)

    def _items_for(self, frame: Frame) -> list[Action]:
        if frame.kind is FrameKind.HOME:
            items = [Action(ActionKind.OPEN_ROOM, room=self.lights_on_room)]
            if self.floors:
                # Rooms stay on one screen, grouped under a floor-header tile.
                for floor in self.floors:
                    items.append(Action(ActionKind.FLOOR_HEADER, floor=floor))
                    items += [Action(ActionKind.OPEN_ROOM, room=r) for r in floor.rooms]
                if self.unassigned_rooms:
                    items.append(Action(ActionKind.FLOOR_HEADER, floor=Floor("__other__", "Other")))
                    items += [Action(ActionKind.OPEN_ROOM, room=r) for r in self.unassigned_rooms]
            else:
                items += [Action(ActionKind.OPEN_ROOM, room=r) for r in self.rooms]
            return items
        # ROOM
        room = frame.room
        if room is not None and room.is_dynamic:
            room.entities = self._collect_on_lights()  # recompute live membership
        entities = room.entities if room else []
        return [Action(ActionKind.ENTITY, entity=e) for e in entities]

    # -- press handling -----------------------------------------------------

    def handle_press(self, key: int, pressed: bool) -> None:
        with self._lock:
            action = self.key_map.get(key)
            if not pressed:
                start = self._press_start.pop(key, None)
                timer = self._hold_timers.pop(key, None)

        if pressed:
            if action is None:
                return
            # Long-press-capable entities (locks) defer to release so we can tell
            # a short press from a long one; everything else fires immediately.
            if action.kind is ActionKind.ENTITY and action.entity is not None and action.entity.has_long_press:
                self._arm_hold(key, action.entity)
                return
            self._dispatch_down(action)
            return

        # release: only meaningful for the deferred long-press-capable keys
        if timer is not None:
            timer.cancel()
        if start is None or action is None:
            return
        if action.kind is ActionKind.ENTITY and action.entity is not None:
            held = time.monotonic() - start
            self._invoke(action.entity, long=action.entity.has_long_press and held >= LONG_PRESS_S)
            self._restore_key(key)  # clear any "release to open" feedback

    def _arm_hold(self, key: int, entity: DeviceEntity) -> None:
        """Record press time and schedule the armed-feedback render."""
        timer = threading.Timer(LONG_PRESS_S, self._show_hold_feedback, args=(key,))
        timer.daemon = True
        with self._lock:
            self._press_start[key] = time.monotonic()
            self._hold_timers[key] = timer
        timer.start()

    def _show_hold_feedback(self, key: int) -> None:
        """Fired by the timer: if the key is still held, show it is armed."""
        with self._lock:
            if self._disconnected or key not in self._press_start:
                return  # released (or disconnected) before the threshold
            self.display.set_image(key, self.renderer.hold_feedback())

    def _restore_key(self, key: int) -> None:
        with self._lock:
            if self._disconnected:
                return
            action = self.key_map.get(key)
            if action is not None:
                self.display.set_image(key, self._image_for(action))

    def _dispatch_down(self, action: Action) -> None:
        if action.kind is ActionKind.OPEN_ROOM:
            self._push(Frame(FrameKind.ROOM, room=action.room))
        elif action.kind is ActionKind.FLOOR_HEADER:
            return  # labels are not interactive
        elif action.kind is ActionKind.BACK:
            self._pop()
        elif action.kind is ActionKind.PAGE:
            self._change_page(action.delta)
        elif action.kind is ActionKind.ENTITY and action.entity is not None:
            if action.entity.is_controllable:
                self._invoke(action.entity, long=False)

    # Public navigation entry points (also used by the export tool).
    def home(self) -> None:
        with self._lock:
            self.stack = [Frame(FrameKind.HOME)]
        self.render()

    def open_room(self, room: Room) -> None:
        with self._lock:
            self.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
        self.render()

    def _push(self, frame: Frame) -> None:
        with self._lock:
            self.stack.append(frame)
        self.render()

    def _pop(self) -> None:
        with self._lock:
            if len(self.stack) > 1:
                self.stack.pop()
        self.render()

    def _change_page(self, delta: int) -> None:
        with self._lock:
            frame = self.stack[-1]
            frame.page = max(0, frame.page + delta)
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

    def _invoke(self, entity: DeviceEntity, long: bool) -> None:
        call = entity.long_press_call() if long else entity.service_call()
        if call is None:  # long press on an entity without a long action -> short
            call = entity.service_call()
        if call is None:
            return
        try:
            self.on_service(call)
        except Exception as exc:  # noqa: BLE001 - a bad call must not kill the deck thread
            logger.warning("Service call %s failed: %s", call, exc)

    # -- live updates -------------------------------------------------------

    def refresh_entity(self, entity_id: str) -> None:
        """Re-render the key showing ``entity_id``, if it is on screen.

        While viewing the dynamic "Lights On" folder, a light toggling changes
        which lights belong there, so the whole view is rebuilt instead.
        """
        with self._lock:
            if self._disconnected:
                return
            frame = self.stack[-1]
            viewing_dynamic = (
                frame.kind is FrameKind.ROOM
                and frame.room is not None
                and frame.room.is_dynamic
                and entity_id.startswith("light.")  # only lights change membership
            )
        if viewing_dynamic:
            self.render()  # _items_for recomputes membership
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


def layout_room(
    controls: list[Action],
    readouts: list[Action],
    total_keys: int,
    cols: int,
    page: int,
) -> dict[int, Action]:
    """Lay out a room: Back at key 0, controls top, read-only sensors bottom.

    Sensors get their own band of rows flush to the bottom of the grid, visually
    separated from the controllable devices in the top rows. When everything
    can't fit on one page, falls back to a paginated sequential layout
    (controls first, then sensors) so nothing is lost.
    """
    back: dict[int, Action] = {0: Action(ActionKind.BACK)}
    rows = total_keys // cols

    sensor_rows = min(_ceil_div(len(readouts), cols), rows - 1) if readouts else 0
    control_rows = rows - sensor_rows
    control_capacity = control_rows * cols - 1  # minus Back at key 0
    sensor_capacity = sensor_rows * cols

    if len(controls) > control_capacity or len(readouts) > sensor_capacity:
        return layout_page(controls + readouts, total_keys, back, page)

    result = dict(back)
    control_slots = [k for k in range(control_rows * cols) if k != 0]
    for slot, action in zip(control_slots, controls):
        result[slot] = action

    sensor_start = (rows - sensor_rows) * cols
    for slot, action in zip(range(sensor_start, total_keys), readouts):
        result[slot] = action
    return result


def _ceil_div(a: int, b: int) -> int:
    return -(-a // b)


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
