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

from ..deck import renderer as renderer_mod
from ..deck.renderer import KeyRenderer
from ..ha.model import DeviceEntity, Floor, Room, Status
from ..ha.weather import ForecastDay, Weather, parse_forecast

logger = logging.getLogger(__name__)

# Sentinel area ids for the virtual home-screen folders.
LIGHTS_ON_AREA = "__lights_on__"
SECURITY_AREA = "__security__"

# Hold at least this long for a press to count as a long press (e.g. open a lock).
LONG_PRESS_S = 0.5


class FrameKind(Enum):
    HOME = auto()
    ROOM = auto()
    SECURITY = auto()
    LIGHT_GRID = auto()
    WEATHER = auto()


@dataclass
class Frame:
    kind: FrameKind
    room: Room | None = None
    page: int = 0
    entity: DeviceEntity | None = None  # the light being edited in a LIGHT_GRID
    forecast: list[ForecastDay] | None = None  # days shown in a WEATHER frame


class ActionKind(Enum):
    OPEN_ROOM = auto()
    OPEN_SECURITY = auto()
    OPEN_WEATHER = auto()
    FLOOR_HEADER = auto()  # non-interactive section label
    ENTITY = auto()
    GRID_CELL = auto()     # a brightness/color-temp preset in the light grid
    WEATHER_DAY = auto()   # non-interactive forecast tile
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
    data: dict | None = None  # GRID_CELL: {"brightness_pct":.., "color_temp_kelvin":..}
    day: ForecastDay | None = None  # WEATHER_DAY tile


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
        weather: Weather | None = None,
        on_forecast: Callable[[str], list[dict]] | None = None,
    ) -> None:
        self.display = display
        self.renderer = renderer
        self.rooms = rooms
        self.on_service = on_service
        self.weather = weather
        self.on_forecast = on_forecast
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
        # Virtual folder gathering all locks, closures and presence sensors,
        # grouped by type (one type per row).
        self.security_folder = Room(area_id=SECURITY_AREA, name="Security", icon="mdi:shield-home")

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
        if action.kind is ActionKind.OPEN_SECURITY:
            return self.renderer.room(action.room, accent=renderer_mod.SECURITY_ACCENT)
        if action.kind is ActionKind.OPEN_WEATHER:
            return self.renderer.weather_button(self.weather)
        if action.kind is ActionKind.WEATHER_DAY:
            return self.renderer.weather_day(action.day)
        if action.kind is ActionKind.OPEN_ROOM:
            accent = renderer_mod.LIGHTS_ACCENT if action.room.is_dynamic else renderer_mod.ROOM_ACCENT
            return self.renderer.room(action.room, accent=accent)
        if action.kind is ActionKind.ENTITY:
            return self.renderer.device(action.entity)
        if action.kind is ActionKind.GRID_CELL:
            d = action.data
            if "hs_color" in d:
                return self.renderer.color_cell(d["hs_color"][0], d["hs_color"][1], d["brightness_pct"])
            return self.renderer.light_cell(d["color_temp_kelvin"], d["brightness_pct"])
        if action.kind is ActionKind.BACK:
            return self.renderer.nav("back")
        if action.kind is ActionKind.PAGE:
            return self.renderer.nav("next" if action.delta > 0 else "prev")
        return self.renderer.blank()

    def _build_key_map(self) -> dict[int, Action]:
        frame = self.stack[-1]
        if frame.kind is FrameKind.ROOM:
            return self._room_key_map(frame)
        if frame.kind is FrameKind.SECURITY:
            return self._security_key_map(frame)
        if frame.kind is FrameKind.LIGHT_GRID:
            return self._light_grid_key_map(frame)
        if frame.kind is FrameKind.WEATHER:
            return self._weather_key_map(frame)
        return self._home_key_map(frame)

    def _weather_key_map(self, frame: Frame) -> dict[int, Action]:
        """Fullscreen forecast: Back at key 0, one tile per upcoming day."""
        result: dict[int, Action] = {0: Action(ActionKind.BACK)}
        for i, day in enumerate(frame.forecast or []):
            key = 1 + i
            if key >= self.display.key_count:
                break
            result[key] = Action(ActionKind.WEATHER_DAY, day=day)
        return result

    def _home_key_map(self, frame: Frame) -> dict[int, Action]:
        """Home view: rooms/floors on top, special folders pinned to the bottom row."""
        content = self._items_for(frame)
        specials = [
            Action(ActionKind.OPEN_ROOM, room=self.lights_on_room),
            Action(ActionKind.OPEN_SECURITY, room=self.security_folder),
        ]
        if self.weather is not None:
            specials.append(Action(ActionKind.OPEN_WEATHER))
        cols = getattr(self.display, "cols", 0)
        if not cols:  # no grid info: content first, specials at the end
            return layout_page(content + specials, self.display.key_count, {}, frame.page)
        return layout_home(content, specials, self.display.key_count, cols, frame.page)

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

    def _security_key_map(self, frame: Frame) -> dict[int, Action]:
        """Security view: locks, closures and presence, one type per row."""
        groups = [
            [Action(ActionKind.ENTITY, entity=e) for e in group]
            for group in self._collect_security_groups()
        ]
        cols = getattr(self.display, "cols", 0)
        if not cols:  # no grid info: flat sequential, types still contiguous
            flat = [a for group in groups for a in group]
            return layout_page(flat, self.display.key_count, {0: Action(ActionKind.BACK)}, frame.page)
        return layout_security(groups, self.display.key_count, cols, frame.page)

    def _light_grid_key_map(self, frame: Frame) -> dict[int, Action]:
        """Full-deck picker: every key is a preset. Rows = brightness (top
        brightest), columns = color temperature across the light's full range.
        Tapping a cell applies it and closes the picker.
        """
        entity = frame.entity
        cols = getattr(self.display, "cols", 0)
        rows = self.display.key_count // cols if cols else 0
        if entity is None or cols < 2 or rows < 2:
            return {}

        # RGB lights get a hue picker; color-temp-only lights get a kelvin picker.
        rgb = entity.supports_rgb_color
        if rgb:
            brightness, hues = entity.color_grid_levels(rows, cols)
        else:
            brightness, kelvins = entity.light_grid_levels(rows, cols)
        brightness_top_down = list(reversed(brightness))  # row 0 = brightest

        result: dict[int, Action] = {}
        for r in range(rows):
            for c in range(cols):
                if rgb:
                    data = {"brightness_pct": brightness_top_down[r], "hs_color": [hues[c], 100]}
                else:
                    data = {"brightness_pct": brightness_top_down[r], "color_temp_kelvin": kelvins[c]}
                result[r * cols + c] = Action(ActionKind.GRID_CELL, entity=entity, data=data)
        return result

    def _collect_security_groups(self) -> list[list[DeviceEntity]]:
        """Locks, then closures, then presence sensors — each sorted, empties dropped."""
        locks, closures, presence = [], [], []
        for room in self.rooms:
            for entity in room.entities:
                if entity.domain == "lock":
                    locks.append(entity)
                elif entity.is_closure:
                    closures.append(entity)
                elif entity.is_presence:
                    presence.append(entity)
        groups = [locks, closures, presence]
        for group in groups:
            group.sort(key=lambda e: e.name.lower())
        return [g for g in groups if g]

    def _items_for(self, frame: Frame) -> list[Action]:
        if frame.kind is FrameKind.HOME:
            # Rooms (grouped under floor headers when HA has floors). The special
            # "Lights On"/"Security" folders are added separately, pinned to the
            # bottom row by _home_key_map.
            items: list[Action] = []
            if self.floors:
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
            entity = action.entity
            long = entity.has_long_press and (time.monotonic() - start) >= LONG_PRESS_S
            if long and self._grid_fits() and (entity.supports_rgb_color or entity.supports_light_grid):
                self._open_light_grid(entity)  # opens the picker; view changes
            else:
                self._invoke(entity, long=long)
                self._restore_key(key)  # clear any hold feedback

    def _arm_hold(self, key: int, entity: DeviceEntity) -> None:
        """Record press time and schedule the armed-feedback render."""
        timer = threading.Timer(LONG_PRESS_S, self._show_hold_feedback, args=(key, entity))
        timer.daemon = True
        with self._lock:
            self._press_start[key] = time.monotonic()
            self._hold_timers[key] = timer
        timer.start()

    def _show_hold_feedback(self, key: int, entity: DeviceEntity) -> None:
        """Fired by the timer: if the key is still held, show it is armed."""
        with self._lock:
            if self._disconnected or key not in self._press_start:
                return  # released (or disconnected) before the threshold
            if entity.supports_rgb_color or entity.supports_light_grid:
                img = self.renderer.hold_feedback("palette", "Release for presets")
            else:
                img = self.renderer.hold_feedback()  # lock: "Release to open"
            self.display.set_image(key, img)

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
        elif action.kind is ActionKind.OPEN_SECURITY:
            self._push(Frame(FrameKind.SECURITY))
        elif action.kind is ActionKind.OPEN_WEATHER:
            self._open_weather()
        elif action.kind is ActionKind.WEATHER_DAY:
            return  # forecast tiles are not interactive
        elif action.kind is ActionKind.FLOOR_HEADER:
            return  # labels are not interactive
        elif action.kind is ActionKind.BACK:
            self._pop()
        elif action.kind is ActionKind.PAGE:
            self._change_page(action.delta)
        elif action.kind is ActionKind.GRID_CELL:
            self._apply_light_cell(action)
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

    def _grid_fits(self) -> bool:
        cols = getattr(self.display, "cols", 0)
        rows = self.display.key_count // cols if cols else 0
        return cols >= 2 and rows >= 2

    def _open_light_grid(self, entity: DeviceEntity) -> None:
        self._push(Frame(FrameKind.LIGHT_GRID, entity=entity))

    def _open_weather(self) -> None:
        if self.weather is None:
            return
        raw: list[dict] = []
        if self.on_forecast is not None:
            try:
                raw = self.on_forecast(self.weather.entity_id)
            except Exception as exc:  # noqa: BLE001 - forecast is best-effort
                logger.warning("Forecast fetch failed: %s", exc)
        self._push(Frame(FrameKind.WEATHER, forecast=parse_forecast(raw)))

    def update_weather(self, state: str, attributes: dict) -> None:
        """Refresh the weather entity and re-render its home button if visible."""
        with self._lock:
            if self.weather is None or self._disconnected:
                return
            self.weather.update(state, attributes)
            for key, action in self.key_map.items():
                if action.kind is ActionKind.OPEN_WEATHER:
                    self.display.set_image(key, self.renderer.weather_button(self.weather))
                    return

    def _apply_light_cell(self, action: Action) -> None:
        call = ("light", "turn_on", action.entity.entity_id, action.data or {})
        try:
            self.on_service(call)
        except Exception as exc:  # noqa: BLE001 - a bad call must not kill the deck thread
            logger.warning("Service call %s failed: %s", call, exc)
        self._pop()  # apply and close the picker

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


def layout_home(
    content: list[Action],
    specials: list[Action],
    total_keys: int,
    cols: int,
    page: int,
) -> dict[int, Action]:
    """Home layout: rooms/floors in the top rows, special folders bottom-left.

    The special folders (Lights On, Security) are pinned to the start of the
    bottom row so they're always in the same place; room/floor content fills the
    rows above and paginates there, with Prev/Next on the bottom-right.
    """
    rows = total_keys // cols
    bottom = (rows - 1) * cols  # first key of the bottom row

    if rows < 2:  # single-row deck: just lay everything out sequentially
        return layout_page(content + specials, total_keys, {}, page)

    result: dict[int, Action] = {}
    for i, action in enumerate(specials):
        result[bottom + i] = action

    content_capacity = bottom  # the top rows (keys 0 .. bottom-1)
    if len(content) <= content_capacity:
        for key, action in enumerate(content):
            result[key] = action
        return result

    page_count = max(1, _ceil_div(len(content), content_capacity))
    page = max(0, min(page, page_count - 1))
    start = page * content_capacity
    for key, action in enumerate(content[start : start + content_capacity]):
        result[key] = action

    # Pagination lives on the bottom-right, clear of the bottom-left specials.
    if page > 0:
        result[total_keys - 2] = Action(ActionKind.PAGE, delta=-1)
    if page < page_count - 1:
        result[total_keys - 1] = Action(ActionKind.PAGE, delta=1)
    return result


def layout_security(
    groups: list[list[Action]],
    total_keys: int,
    cols: int,
    page: int,
) -> dict[int, Action]:
    """Lay out the Security view: Back in column 0, one entity type per column.

    Each group (locks / closures / presence) gets its own column, entities
    stacking top-to-bottom; a group with more than ``rows`` entities wraps into
    additional columns — so no column ever mixes two types. Column 0 holds Back
    (and Prev/Next when there are more columns than fit). Falls back to a flat
    sequential layout on very short decks.
    """
    back: dict[int, Action] = {0: Action(ActionKind.BACK)}
    rows = total_keys // cols
    content_cols = cols - 1  # column 0 is reserved for Back/navigation

    if rows < 3 or content_cols < 1:  # too small for the banded layout + col-0 nav
        flat = [a for group in groups for a in group]
        return layout_page(flat, total_keys, back, page)

    # One "column" per chunk of `rows` entities; a tall group spans several.
    columns: list[list[Action]] = []
    for group in groups:
        for start in range(0, len(group), rows):
            columns.append(group[start : start + rows])

    result = dict(back)
    page_count = max(1, _ceil_div(len(columns), content_cols))
    page = max(0, min(page, page_count - 1))

    if page_count > 1:  # navigation tucked into the bottom of the (empty) column 0
        if page > 0:
            result[(rows - 2) * cols] = Action(ActionKind.PAGE, delta=-1)
        if page < page_count - 1:
            result[(rows - 1) * cols] = Action(ActionKind.PAGE, delta=1)

    page_columns = columns[page * content_cols : (page + 1) * content_cols]
    for col_offset, column in enumerate(page_columns):
        col = 1 + col_offset
        for row, action in enumerate(column):
            result[row * cols + col] = action
    return result


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
