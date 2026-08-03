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

import io
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, tzinfo
from enum import Enum, auto
from typing import Callable

from PIL import Image

from .. import state
from ..deck import renderer as renderer_mod
from ..deck.renderer import KeyRenderer
from ..ha.calendar import (
    AGENDA_DAYS,
    Calendar,
    CalendarDay,
    CalendarEvent,
    events_from_calendars,
    group_by_day,
    next_event,
    parse_events,
)
from ..ha.history import HistoryEvent, parse_logbook
from ..ha.model import DeviceEntity, Floor, Room, Status
from ..ha.weather import ForecastDay, Weather, parse_forecast

logger = logging.getLogger(__name__)

# Sentinel area ids for the virtual home-screen folders.
LIGHTS_ON_AREA = "__lights_on__"
SECURITY_AREA = "__security__"
CLIMATE_AREA = "__climate__"
SETTINGS_AREA = "__settings__"

# Hold at least this long for a press to count as a long press (e.g. open a lock).
LONG_PRESS_S = 0.5


class FrameKind(Enum):
    HOME = auto()
    ROOM = auto()
    SECURITY = auto()
    CLIMATE = auto()
    ENTITY_MENU = auto()    # long-press options menu (lights / locks)
    PICKER = auto()         # single-dimension swatch grid (brightness/color/temp/%)
    FAN = auto()            # fan controls: toggle + speed presets / picker
    COVER_ACTIONS = auto()  # cover controls: toggle + open/stop/close + position
    MEDIA = auto()          # media player: now playing + transport + volume
    ALARM = auto()          # alarm panel: disarm / arm modes
    CLIMATE_DETAIL = auto()  # thermostat: temperature + presets
    WEATHER = auto()
    CALENDAR = auto()         # agenda: upcoming events across the chosen calendars
    CALENDAR_PICKER = auto()  # multi-select: which calendars feed the tile
    HISTORY = auto()
    TIMER = auto()
    SETTINGS = auto()


@dataclass
class Frame:
    kind: FrameKind
    room: Room | None = None
    page: int = 0
    entity: DeviceEntity | None = None  # entity a menu/picker/detail view acts on
    forecast: list[ForecastDay] | None = None  # days shown in a WEATHER frame
    history: list[HistoryEvent] | None = None  # events shown in a HISTORY frame
    events: list[CalendarEvent] | None = None  # events shown in a CALENDAR frame
    data: dict | None = None  # PICKER: {"type": "brightness"|"color"|...}


class ActionKind(Enum):
    OPEN_ROOM = auto()
    OPEN_SECURITY = auto()
    OPEN_CLIMATE = auto()
    OPEN_WEATHER = auto()
    OPEN_CALENDAR = auto()   # calendar tile: press for the agenda, hold to pick calendars
    OPEN_SETTINGS = auto()
    CLOCK = auto()          # live local-time tile (non-interactive)
    DATE = auto()           # live date + weekday tile (non-interactive)
    CLIMATE_TEMP = auto()  # a temperature sensor tile, labelled by its room
    SETTINGS_ITEM = auto()  # an action button inside the Settings folder
    FLOOR_HEADER = auto()  # non-interactive section label
    ENTITY = auto()
    MENU_ITEM = auto()     # an option in the long-press entity menu
    PICKER_CELL = auto()   # a swatch/level in a picker (applies + closes)
    SERVICE_BUTTON = auto()  # a button that fires a service call (preset / cover)
    WEATHER_DAY = auto()   # non-interactive forecast tile (compact fallback)
    WEATHER_CELL = auto()  # one cell of the full-matrix forecast (day/icon/min/max)
    HISTORY_TITLE = auto() # header of the history view (entity name)
    HISTORY_EVENT = auto() # one timeline entry in the history view
    CALENDAR_DAY = auto()     # agenda column header: weekday + date (non-interactive)
    CALENDAR_EVENT = auto()   # one entry in the agenda view (non-interactive)
    CALENDAR_TOGGLE = auto()  # a calendar in the picker (tap toggles it on/off)
    TIMER_STATUS = auto()  # remaining-time display in the timer detail view
    TIMER_ACTION = auto()  # pause/resume/cancel/finish button
    CLIMATE_STATUS = auto()  # target/current-temp display in the thermostat view
    CLIMATE_ADJUST = auto()  # +/- target-temperature button
    CLIMATE_POWER = auto()   # turn the thermostat on/off
    MEDIA_ART = auto()       # one cell of the 3x3 album-art mosaic (non-interactive)
    MEDIA_TEXT = auto()      # song / artist / album text tile (non-interactive)
    MEDIA_VOLUME = auto()    # volume level readout (non-interactive)
    ALARM_STATUS = auto()    # alarm state display (non-interactive)
    BACK = auto()
    PAGE = auto()
    BLANK = auto()
    RESERVED_BLANK = auto()  # filler tile in the special-folder band (contrasted bg)


@dataclass
class Action:
    kind: ActionKind
    floor: Floor | None = None
    room: Room | None = None
    entity: DeviceEntity | None = None
    delta: int = 0
    data: dict | None = None  # PICKER_CELL: {"call":.., "render":..}; MENU_ITEM/SERVICE_BUTTON payloads
    day: ForecastDay | None = None  # WEATHER_DAY tile
    event: HistoryEvent | CalendarEvent | None = None  # HISTORY_EVENT / CALENDAR_EVENT tile


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
        calendars: list[Calendar] | None = None,
        on_calendar_events: Callable[[list[str], int], dict[str, list[dict]]] | None = None,
        agenda_days: int = AGENDA_DAYS,
        on_forecast: Callable[[str], list[dict]] | None = None,
        on_logbook: Callable[[str], list[dict]] | None = None,
        on_reload: Callable[[], None] | None = None,
        on_rotate: Callable[[], None] | None = None,
        on_brightness: Callable[[], None] | None = None,
        on_media_image: Callable[[str], bytes | None] | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.display = display
        self.renderer = renderer
        self.rooms = rooms
        self.on_service = on_service
        self.weather = weather
        self.calendars = calendars or []
        self.on_calendar_events = on_calendar_events
        self.agenda_days = max(1, agenda_days)  # days of agenda to fetch and lay out
        self.on_forecast = on_forecast
        self.on_logbook = on_logbook
        self.on_reload = on_reload
        self.on_rotate = on_rotate
        self.on_brightness = on_brightness
        self.on_media_image = on_media_image  # fn(entity_picture_url) -> raw image bytes
        self.tz = tz  # HA timezone for history clock labels (None = container local)
        self._media_art: dict[str, Image.Image] = {}   # entity_picture url -> decoded art
        self._art_inflight: set[str] = set()           # urls currently being fetched
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
        # Virtual folder gathering temperature sensors, fans and thermostats.
        self.climate_folder = Room(area_id=CLIMATE_AREA, name="Climate", icon="mdi:home-thermometer")
        # Deck settings folder, always pinned last.
        self.settings_folder = Room(area_id=SETTINGS_AREA, name="Settings", icon="mdi:cog")

        self.stack: list[Frame] = [Frame(FrameKind.HOME)]
        self._collapsed_floors: set[str] = set()  # floor_ids whose rooms are hidden
        self.key_map: dict[int, Action] = {}
        self._press_start: dict[int, float] = {}  # key -> press-down time, for long-press keys
        self._hold_timers: dict[int, threading.Timer] = {}  # key -> armed-feedback timer
        self._lock = threading.RLock()
        self._disconnected = False
        self._clock_shown = ""  # last clock/date text drawn, so tick() only redraws on change
        self._date_shown = ""
        self._calendar_shown = ""  # same, for the calendar tile's next-event text
        # Which calendars feed the tile. None = "never chosen" -> all of them.
        saved_calendars = state.load().get("calendars")
        self._enabled_calendars: set[str] | None = (
            {str(eid) for eid in saved_calendars} if isinstance(saved_calendars, list) else None
        )

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
            collapsed = action.floor.floor_id in self._collapsed_floors
            return self.renderer.floor_header(action.floor, collapsed=collapsed)
        if action.kind is ActionKind.OPEN_SECURITY:
            return self.renderer.room(action.room, accent=renderer_mod.SECURITY_ACCENT, bg=renderer_mod.RESERVED_BG)
        if action.kind is ActionKind.OPEN_CLIMATE:
            return self.renderer.room(action.room, accent=renderer_mod.CLIMATE_ACCENT, bg=renderer_mod.RESERVED_BG)
        if action.kind is ActionKind.CLIMATE_TEMP:
            return self.renderer.climate_room_reading(action.entity, action.room)
        if action.kind is ActionKind.OPEN_WEATHER:
            return self.renderer.weather_button(self.weather, bg=renderer_mod.RESERVED_BG)
        if action.kind is ActionKind.OPEN_CALENDAR:
            return self.renderer.calendar_button(self._next_event(), bg=renderer_mod.RESERVED_BG)
        if action.kind is ActionKind.CALENDAR_DAY:
            d = action.data or {}
            return self.renderer.calendar_day_header(d.get("label", ""), d.get("date", ""))
        if action.kind is ActionKind.CALENDAR_EVENT:
            # The countdown is only worth a band on today's column; other columns
            # are already labelled by their day header.
            return self.renderer.calendar_event(
                action.event, show_relative=bool((action.data or {}).get("today", True)))
        if action.kind is ActionKind.CALENDAR_TOGGLE:
            d = action.data or {}
            active = bool(d.get("active"))
            return self.renderer.option_button(
                "calendar-check" if active else "calendar-blank", d["label"],
                renderer_mod.CALENDAR_ACCENT, active=active,
            )
        if action.kind is ActionKind.OPEN_SETTINGS:
            return self.renderer.room(action.room, accent=renderer_mod.SETTINGS_ACCENT, bg=renderer_mod.RESERVED_BG)
        if action.kind is ActionKind.CLOCK:
            return self.renderer.clock_tile(datetime.now(self.tz))
        if action.kind is ActionKind.DATE:
            return self.renderer.date_tile(datetime.now(self.tz))
        if action.kind is ActionKind.SETTINGS_ITEM:
            d = action.data or {}
            return self.renderer.action_button(d["icon"], d["label"], d["color"])
        if action.kind is ActionKind.WEATHER_DAY:
            return self.renderer.weather_day(action.day)
        if action.kind is ActionKind.WEATHER_CELL:
            part = (action.data or {}).get("part")
            if part == "day":
                return self.renderer.weather_label_cell(action.day)
            if part == "icon":
                return self.renderer.weather_icon_cell(action.day)
            if part == "min":
                return self.renderer.weather_temp_cell(action.day.low_text(), "min")
            return self.renderer.weather_temp_cell(action.day.high_text(), "max")
        if action.kind is ActionKind.OPEN_ROOM:
            if action.room.is_dynamic:  # the "Lights On" special folder (in the band)
                return self.renderer.room(action.room, accent=renderer_mod.LIGHTS_ACCENT,
                                          bg=renderer_mod.RESERVED_BG)
            return self._render_room_tile(action.room)
        if action.kind is ActionKind.RESERVED_BLANK:
            return self.renderer.reserved_blank()
        if action.kind is ActionKind.ENTITY:
            if action.entity.is_media_player:
                return self.renderer.media_device(action.entity, self._media_art_for(action.entity))
            return self.renderer.device(action.entity)
        if action.kind is ActionKind.MEDIA_ART:
            cell = tuple((action.data or {}).get("cell", (1, 1)))
            return self.renderer.media_art_tile(action.entity, self._media_art_for(action.entity), cell)
        if action.kind is ActionKind.MEDIA_TEXT:
            field = (action.data or {}).get("field")
            caption, value = {
                "title": ("Song", action.entity.media_title),
                "artist": ("Artist", action.entity.media_artist),
                "album": ("Album", action.entity.media_album),
            }[field]
            return self.renderer.media_meta(caption, value)
        if action.kind is ActionKind.MEDIA_VOLUME:
            return self.renderer.media_volume(action.entity)
        if action.kind is ActionKind.ALARM_STATUS:
            return self.renderer.alarm_status(action.entity)
        if action.kind is ActionKind.MENU_ITEM:
            d = action.data or {}
            if d.get("target") == "toggle":  # the Toggle tile reflects the live status
                return self.renderer.toggle_button(action.entity)
            return self.renderer.option_button(d["icon"], d["label"], renderer_mod.ROOM_ACCENT)
        if action.kind is ActionKind.PICKER_CELL:
            return self._render_picker_cell(action.data or {})
        if action.kind is ActionKind.SERVICE_BUTTON:
            d = action.data or {}
            return self.renderer.option_button(d["icon"], d["label"], d.get("color", renderer_mod.NAV_COLOR),
                                                active=d.get("active", False))
        if action.kind is ActionKind.HISTORY_TITLE:
            return self.renderer.history_title(action.entity)
        if action.kind is ActionKind.HISTORY_EVENT:
            return self.renderer.history_event(action.event)
        if action.kind is ActionKind.TIMER_STATUS:
            return self.renderer.timer_status(action.entity)
        if action.kind is ActionKind.TIMER_ACTION:
            d = action.data or {}
            return self.renderer.action_button(d["icon"], d["label"], d["color"])
        if action.kind is ActionKind.CLIMATE_STATUS:
            return self.renderer.climate_status(action.entity)
        if action.kind is ActionKind.CLIMATE_ADJUST:
            d = action.data or {}
            return self.renderer.action_button(d["icon"], d["label"], renderer_mod.CLIMATE_ACCENT)
        if action.kind is ActionKind.CLIMATE_POWER:
            return self.renderer.climate_power(action.entity)
        if action.kind is ActionKind.BACK:
            return self.renderer.nav("back")
        if action.kind is ActionKind.PAGE:
            return self.renderer.nav("next" if action.delta > 0 else "prev")
        return self.renderer.blank()

    def _render_picker_cell(self, data: dict):
        r = data.get("render", {})
        t = r.get("type")
        if t == "temp":
            return self.renderer.temp_cell(r["kelvin"])
        if t == "color":
            return self.renderer.color_swatch(r["hue"], r["sat"])
        if t == "brightness":
            return self.renderer.brightness_cell(tuple(r["base"]), r["pct"])
        return self.renderer.percent_cell(r["pct"])

    def _media_art_for(self, entity: DeviceEntity) -> Image.Image | None:
        """Cached album art for ``entity``; fetches it in the background on a miss.

        Never blocks the render: on a cache miss it kicks off a fetch thread and
        returns None (the tile shows the fallback icon), then re-renders once the
        artwork arrives.
        """
        url = entity.entity_picture
        if not url or self.on_media_image is None:
            return None
        with self._lock:
            cached = self._media_art.get(url)
            if cached is not None:
                return cached
            if url in self._art_inflight:
                return None
            self._art_inflight.add(url)
        threading.Thread(target=self._fetch_art, args=(url,), name="media-art", daemon=True).start()
        return None

    def _fetch_art(self, url: str) -> None:
        image: Image.Image | None = None
        try:
            data = self.on_media_image(url)
            if data:
                img = Image.open(io.BytesIO(data)).convert("RGB")
                img.load()
                image = img
        except Exception as exc:  # noqa: BLE001 - artwork is best-effort
            logger.info("Album art fetch/decode failed: %s", exc)
        with self._lock:
            self._art_inflight.discard(url)
            if image is not None:
                self._media_art[url] = image
        if image is not None:
            self.render()  # redraw so the artwork appears

    def _build_key_map(self) -> dict[int, Action]:
        frame = self.stack[-1]
        if frame.kind is FrameKind.ROOM:
            return self._room_key_map(frame)
        if frame.kind is FrameKind.SECURITY:
            return self._security_key_map(frame)
        if frame.kind is FrameKind.CLIMATE:
            return self._climate_key_map(frame)
        if frame.kind is FrameKind.ENTITY_MENU:
            return self._entity_menu_key_map(frame)
        if frame.kind is FrameKind.PICKER:
            return self._picker_key_map(frame)
        if frame.kind is FrameKind.FAN:
            return self._fan_key_map(frame)
        if frame.kind is FrameKind.COVER_ACTIONS:
            return self._cover_actions_key_map(frame)
        if frame.kind is FrameKind.MEDIA:
            return self._media_key_map(frame)
        if frame.kind is FrameKind.ALARM:
            return self._alarm_key_map(frame)
        if frame.kind is FrameKind.CLIMATE_DETAIL:
            return self._climate_detail_key_map(frame)
        if frame.kind is FrameKind.WEATHER:
            return self._weather_key_map(frame)
        if frame.kind is FrameKind.CALENDAR:
            return self._calendar_key_map(frame)
        if frame.kind is FrameKind.CALENDAR_PICKER:
            return self._calendar_picker_key_map(frame)
        if frame.kind is FrameKind.HISTORY:
            return self._history_key_map(frame)
        if frame.kind is FrameKind.TIMER:
            return self._timer_key_map(frame)
        if frame.kind is FrameKind.SETTINGS:
            return self._settings_key_map(frame)
        return self._home_key_map(frame)

    def _settings_key_map(self, frame: Frame) -> dict[int, Action]:
        """Deck settings: Back, then one button per setting."""
        rotation = getattr(self.display, "rotation", 0)
        brightness = getattr(self.display, "brightness", 0)
        return {
            0: Action(ActionKind.BACK),
            1: Action(ActionKind.SETTINGS_ITEM,
                      data={"action": "reload", "label": "Reload", "icon": "cloud-refresh",
                            "color": renderer_mod.WEATHER_ACCENT}),
            2: Action(ActionKind.SETTINGS_ITEM,
                      data={"action": "rotate", "label": f"Rotate\n{rotation}°", "icon": "screen-rotation",
                            "color": renderer_mod.SETTINGS_ACCENT}),
            3: Action(ActionKind.SETTINGS_ITEM,
                      data={"action": "brightness", "label": f"Brightness\n{brightness}%",
                            "icon": "brightness-6", "color": renderer_mod.ACCENT}),
        }

    def _history_tile(self, entity: DeviceEntity | None) -> Action:
        """A History option tile (opens the full-screen logbook when pressed)."""
        return Action(ActionKind.MENU_ITEM, entity=entity,
                      data={"target": "history", "label": "History", "icon": "history"})

    def _preset_buttons(self, entity: DeviceEntity | None) -> list[Action]:
        """One preset-mode button per ``preset_modes`` (fan or thermostat); the
        active preset is highlighted. Tapping applies it and stays in the view."""
        active = entity.preset_mode if entity else None
        return [
            Action(ActionKind.SERVICE_BUTTON, entity=entity, data={
                "call": (entity.domain, "set_preset_mode", entity.entity_id, {"preset_mode": name}),
                "label": name.replace("_", " ").title(),
                "icon": renderer_mod.PRESET_ICONS.get(name.lower(), "tune"),
                "color": renderer_mod.CLIMATE_ACCENT, "active": name == active, "close": False,
            })
            for name in (entity.preset_modes if entity else [])
        ]

    def _place(self, result: dict[int, Action], tiles: list[Action], start: int) -> None:
        """Place ``tiles`` sequentially from key ``start``, stopping at the deck edge."""
        for i, tile in enumerate(tiles):
            key = start + i
            if key >= self.display.key_count:
                break
            result[key] = tile

    def _timer_key_map(self, frame: Frame) -> dict[int, Action]:
        """Timer controls: Back, remaining-time status, Pause/Resume, Cancel, Finish, History."""
        entity = frame.entity
        state = (entity.state or "").lower() if entity else ""
        if state == "active":
            primary = {"service": "pause", "label": "Pause", "icon": "pause", "color": renderer_mod.ACCENT}
        else:
            primary = {
                "service": "start",
                "label": "Resume" if state == "paused" else "Start",
                "icon": "play", "color": renderer_mod.SECURE,
            }
        return {
            0: Action(ActionKind.BACK),
            1: Action(ActionKind.TIMER_STATUS, entity=entity),
            2: Action(ActionKind.TIMER_ACTION, entity=entity, data=primary),
            3: Action(ActionKind.TIMER_ACTION, entity=entity,
                      data={"service": "cancel", "label": "Cancel", "icon": "close", "color": renderer_mod.UNAVAILABLE}),
            4: Action(ActionKind.TIMER_ACTION, entity=entity,
                      data={"service": "finish", "label": "Finish", "icon": "flag-checkered", "color": renderer_mod.WEATHER_ACCENT}),
            5: self._history_tile(entity),
        }

    def _climate_detail_key_map(self, frame: Frame) -> dict[int, Action]:
        """Thermostat controls: Back, a target/current-temp status, −/+ whole-degree
        set-point buttons, an on/off toggle, then the preset modes and History."""
        entity = frame.entity
        result = {
            0: Action(ActionKind.BACK),
            1: Action(ActionKind.CLIMATE_STATUS, entity=entity),
            2: Action(ActionKind.CLIMATE_ADJUST, entity=entity, delta=-1,
                      data={"label": "−1°", "icon": "thermometer-minus"}),
            3: Action(ActionKind.CLIMATE_ADJUST, entity=entity, delta=1,
                      data={"label": "+1°", "icon": "thermometer-plus"}),
            4: Action(ActionKind.CLIMATE_POWER, entity=entity),
        }
        self._place(result, self._preset_buttons(entity) + [self._history_tile(entity)], start=5)
        return result

    def _fan_key_map(self, frame: Frame) -> dict[int, Action]:
        """Fan controls: Back, Toggle, the speed presets (or a Speed picker button
        when the fan only exposes a percentage), then History."""
        entity = frame.entity
        result = {
            0: Action(ActionKind.BACK),
            1: Action(ActionKind.MENU_ITEM, entity=entity,
                      data={"target": "toggle", "label": "Toggle", "icon": "toggle-switch-variant"}),
        }
        if entity is not None and entity.preset_modes:
            speed = self._preset_buttons(entity)
        else:
            speed = [Action(ActionKind.MENU_ITEM, entity=entity,
                            data={"target": "fan_speed", "label": "Speed", "icon": "fan-speed-1"})]
        self._place(result, speed + [self._history_tile(entity)], start=2)
        return result

    def _entity_menu_key_map(self, frame: Frame) -> dict[int, Action]:
        """Long-press options menu: Back, then one tile per capability."""
        options = self._entity_menu_options(frame.entity)
        return layout_page(options, self.display.key_count, {0: Action(ActionKind.BACK)}, frame.page)

    def _entity_menu_options(self, entity: DeviceEntity | None) -> list[Action]:
        """Capability tiles for the button-menu (History is always last).

        Only lights and locks use this menu: their controls each need the whole
        screen (light pickers) or fire directly (lock). Every other controllable
        type opens a combined control view directly on long press
        (see ``_open_entity_menu``); read-only entities skip straight to History.
        """
        def item(target: str, label: str, icon: str) -> Action:
            return Action(ActionKind.MENU_ITEM, entity=entity,
                          data={"target": target, "label": label, "icon": icon})

        opts: list[Action] = []
        if entity is not None:
            d = entity.domain
            if entity.is_toggleable:
                opts.append(item("toggle", "Toggle", "toggle-switch-variant"))
            if d == "light":
                if entity.supports_brightness:
                    opts.append(item("brightness", "Brightness", "brightness-6"))
                if entity.supports_rgb_color:
                    opts.append(item("color", "Color", "palette"))
                if entity.supports_color_temp:
                    opts.append(item("temperature", "Warmth", "thermometer-lines"))
            elif d == "lock":
                opts.append(item("lock_open", "Open Door", "door-open"))
        opts.append(item("history", "History", "history"))
        return opts

    def _picker_key_map(self, frame: Frame) -> dict[int, Action]:
        """A single-dimension picker (Back + swatches that apply and close)."""
        result: dict[int, Action] = {0: Action(ActionKind.BACK)}
        cells = self._picker_cells(frame.entity, (frame.data or {}).get("type"))
        for i, cell in enumerate(cells):
            key = 1 + i
            if key >= self.display.key_count:
                break
            result[key] = cell
        return result

    def _picker_cells(self, entity: DeviceEntity | None, ptype: str | None) -> list[Action]:
        if entity is None:
            return []
        n = self.display.key_count - 1  # cells fill keys 1..
        eid = entity.entity_id

        def cell(call, render):
            return Action(ActionKind.PICKER_CELL, entity=entity, data={"call": call, "render": render})

        if ptype == "brightness":
            base = entity.base_color() or renderer_mod.WARM_WHITE
            return [cell(("light", "turn_on", eid, {"brightness_pct": b}),
                         {"type": "brightness", "base": list(base), "pct": b})
                    for b in _spread(10, 100, n)]
        if ptype == "temperature":
            lo, hi = entity.color_temp_range()
            return [cell(("light", "turn_on", eid, {"color_temp_kelvin": k}),
                         {"type": "temp", "kelvin": k})
                    for k in _spread(lo, hi, n)]
        if ptype == "color":
            return [cell(("light", "turn_on", eid, {"hs_color": [h, 100]}),
                         {"type": "color", "hue": h, "sat": 100})
                    for h in _hues(n)]
        if ptype == "fan_percentage":
            return [cell(entity.fan_set_percentage_call(p), {"type": "percent", "pct": p})
                    for p in _spread(10, 100, n)]
        if ptype == "cover_position":
            return [cell(entity.cover_set_position_call(p), {"type": "percent", "pct": p})
                    for p in _spread(0, 100, n)]
        return []

    def _media_key_map(self, frame: Frame) -> dict[int, Action]:
        """Now Playing: the album art as a 3x3 mosaic, with the transport buttons
        grouped on one line and the volume settings grouped on another."""
        entity = frame.entity
        key_count = self.display.key_count
        cols = getattr(self.display, "cols", 0) or key_count
        rows = key_count // cols if cols else 1

        def sbtn(call, label, icon, color=renderer_mod.NAV_COLOR):
            return Action(ActionKind.SERVICE_BUTTON, entity=entity, data={
                "call": call, "label": label, "icon": icon, "color": color, "active": False, "close": False})

        transport: list[Action] = []
        volume: list[Action] = []
        if entity is not None:
            if entity.supports_media_previous:
                transport.append(sbtn(entity.media_service_call("media_previous_track"), "Prev", "skip-previous"))
            # Play/pause button matches state: shows Pause while playing, Play otherwise.
            pp_icon, pp_label = ("pause", "Pause") if entity.is_playing else ("play", "Play")
            transport.append(sbtn(entity.media_service_call("media_play_pause"), pp_label, pp_icon, renderer_mod.ACCENT))
            if entity.supports_media_next:
                transport.append(sbtn(entity.media_service_call("media_next_track"), "Next", "skip-next"))
            if entity.supports_media_stop:
                transport.append(sbtn(entity.media_service_call("media_stop"), "Stop", "stop", renderer_mod.UNAVAILABLE))
            if entity.supports_volume:
                volume.append(sbtn(entity.media_service_call("volume_down"), "Vol −", "volume-minus"))
            if entity.supports_volume_mute:
                volume.append(sbtn(entity.volume_mute_call(not entity.is_muted),
                                   "Unmute" if entity.is_muted else "Mute",
                                   "volume-off" if entity.is_muted else "volume-mute"))
            if entity.supports_volume:
                volume.append(sbtn(entity.media_service_call("volume_up"), "Vol +", "volume-plus"))
            if volume:
                volume.append(Action(ActionKind.MEDIA_VOLUME, entity=entity))

        history = Action(ActionKind.MENU_ITEM, entity=entity,
                         data={"target": "history", "label": "History", "icon": "history"})

        if entity is None or cols < 3 or rows < 3:  # too small for the mosaic: flat fallback
            result = {0: Action(ActionKind.BACK)}
            for i, action in enumerate([Action(ActionKind.MEDIA_ART, entity=entity, data={"cell": [1, 1]})]
                                       + transport + volume + [history]):
                if 1 + i < key_count:
                    result[1 + i] = action
            return result

        result: dict[int, Action] = {}
        for r in range(3):  # the artwork fills a 3x3 block in the top-left
            for c in range(3):
                result[r * cols + c] = Action(ActionKind.MEDIA_ART, entity=entity, data={"cell": [r, c]})
        for c, field in enumerate(("title", "artist", "album")):  # song / artist / album below it
            result[3 * cols + c] = Action(ActionKind.MEDIA_TEXT, entity=entity, data={"field": field})
        result[3] = Action(ActionKind.BACK)   # nav column, beside the artwork
        result[cols + 3] = history
        # Transport on one line, volume on the next: below the metadata (portrait) or to the right.
        if rows >= 6:
            transport_start, volume_start = 4 * cols, 5 * cols
        else:
            transport_start, volume_start = 4, cols + 4
        for line, start in ((transport, transport_start), (volume, volume_start)):
            for i, action in enumerate(line):
                if start + i < key_count:
                    result[start + i] = action
        return result

    def _alarm_key_map(self, frame: Frame) -> dict[int, Action]:
        """Alarm panel: Back, a status tile, then Disarm + the supported arm
        modes (the active one highlighted), and History."""
        entity = frame.entity
        result: dict[int, Action] = {0: Action(ActionKind.BACK), 1: Action(ActionKind.ALARM_STATUS, entity=entity)}
        if entity is None:
            return result
        state = (entity.state or "").lower()

        def sbtn(call, label, icon, color, active):
            return Action(ActionKind.SERVICE_BUTTON, entity=entity, data={
                "call": call, "label": label, "icon": icon, "color": color, "active": active, "close": False})

        buttons = [sbtn(entity.alarm_disarm_call(), "Disarm", "shield-off",
                        renderer_mod.NAV_COLOR, active=state == "disarmed")]
        modes = [("home", "Home", "shield-home", entity.supports_arm_home),
                 ("away", "Away", "shield-lock", entity.supports_arm_away),
                 ("night", "Night", "shield-moon", entity.supports_arm_night),
                 ("vacation", "Vacation", "shield-airplane", entity.supports_arm_vacation)]
        for mode, label, icon, supported in modes:
            if supported:
                buttons.append(sbtn(entity.alarm_arm_call(mode), label, icon,
                                    renderer_mod.SECURE, active=state == f"armed_{mode}"))
        buttons.append(Action(ActionKind.MENU_ITEM, entity=entity,
                              data={"target": "history", "label": "History", "icon": "history"}))

        for i, action in enumerate(buttons):
            key = 2 + i
            if key >= self.display.key_count:
                break
            result[key] = action
        return result

    def _cover_actions_key_map(self, frame: Frame) -> dict[int, Action]:
        """Cover controls: Back, Open / Stop / Close (these supersede a plain
        toggle), then Position (when supported) and History."""
        entity = frame.entity
        eid = entity.entity_id if entity else ""

        def btn(service, label, icon, color):
            return Action(ActionKind.SERVICE_BUTTON, entity=entity, data={
                "call": ("cover", service, eid, {}), "label": label, "icon": icon,
                "color": color, "active": False, "close": False})

        result = {
            0: Action(ActionKind.BACK),
            1: btn("open_cover", "Open", "arrow-up-bold", renderer_mod.ACCENT),
            2: btn("stop_cover", "Stop", "stop", renderer_mod.UNAVAILABLE),
            3: btn("close_cover", "Close", "arrow-down-bold", renderer_mod.NAV_COLOR),
        }
        trailing: list[Action] = []
        if entity is not None and entity.supports_cover_position:
            trailing.append(Action(ActionKind.MENU_ITEM, entity=entity,
                                   data={"target": "position", "label": "Position", "icon": "arrow-expand-vertical"}))
        trailing.append(self._history_tile(entity))
        self._place(result, trailing, start=4)
        return result

    def _history_key_map(self, frame: Frame) -> dict[int, Action]:
        """History view: Back, an entity-name header, then newest-first events."""
        result: dict[int, Action] = {0: Action(ActionKind.BACK)}
        result[1] = Action(ActionKind.HISTORY_TITLE, entity=frame.entity)
        for i, event in enumerate(frame.history or []):
            key = 2 + i
            if key >= self.display.key_count:
                break
            result[key] = Action(ActionKind.HISTORY_EVENT, event=event)
        return result

    def _weather_key_map(self, frame: Frame) -> dict[int, Action]:
        """Fullscreen forecast, Back at key 0. The 4 cells per day are day /
        icon / max / min, laid out one day per **column** in landscape and one
        day per **row** in portrait (so the taller grid isn't wasted)."""
        days = frame.forecast or []
        cols = getattr(self.display, "cols", 0)
        rows = self.display.key_count // cols if cols else 0

        def cell(day, part):
            return Action(ActionKind.WEATHER_CELL, day=day, data={"part": part})

        result: dict[int, Action] = {0: Action(ActionKind.BACK)}

        if rows > cols and cols >= 4:
            # Portrait: one day per row -> [weekday][icon][max][min], rows 1..
            for i, day in enumerate(days):
                r = 1 + i
                if r >= rows:
                    break
                base = r * cols
                result[base + 0] = cell(day, "day")
                result[base + 1] = cell(day, "icon")
                result[base + 2] = cell(day, "max")
                result[base + 3] = cell(day, "min")
            return result

        if rows >= 4:
            # Landscape: one day per column, rows = day / icon / max / min.
            for i, day in enumerate(days[: cols - 1]):
                c = i + 1
                result[0 * cols + c] = cell(day, "day")
                result[1 * cols + c] = cell(day, "icon")
                result[2 * cols + c] = cell(day, "max")
                result[3 * cols + c] = cell(day, "min")
            return result

        # Small deck: one compact tile per day.
        for i, day in enumerate(days[: self.display.key_count - 1]):
            result[1 + i] = Action(ActionKind.WEATHER_DAY, day=day)
        return result

    # -- calendars ----------------------------------------------------------

    def _enabled_ids(self) -> set[str]:
        """Entity ids of the calendars feeding the tile (all of them until picked)."""
        if self._enabled_calendars is None:
            return {c.entity_id for c in self.calendars}
        return set(self._enabled_calendars)

    def _active_calendars(self) -> list[Calendar]:
        enabled = self._enabled_ids()
        return [c for c in self.calendars if c.entity_id in enabled]

    def _next_event(self) -> CalendarEvent | None:
        """The current-or-next event across the chosen calendars."""
        return next_event(self._active_calendars(), datetime.now(self.tz), self.tz)

    def _calendar_key_map(self, frame: Frame) -> dict[int, Action]:
        """Agenda: Back at key 0, then one column per day under its date header.

        Every day of the window gets a column, empty or not, so the days stay in
        the same place and the grid reads as a week.
        """
        days = group_by_day(frame.events or [], datetime.now(self.tz), self.tz, span=self.agenda_days)
        cols = getattr(self.display, "cols", 0)
        if not cols:  # no grid info: flat sequential, days still contiguous
            flat = [Action(ActionKind.CALENDAR_EVENT, event=e, data={"today": i == 0})
                    for i, day in enumerate(days) for e in day.events]
            # An empty agenda still gets one tile, so the view isn't a lone Back key.
            fixed = {0: Action(ActionKind.BACK)}
            return layout_page(flat or [Action(ActionKind.CALENDAR_EVENT)],
                               self.display.key_count, fixed, frame.page)
        return layout_calendar(days, self.display.key_count, cols, frame.page)

    def _calendar_picker_key_map(self, frame: Frame) -> dict[int, Action]:
        """Multi-select: one tile per calendar, the chosen ones highlighted."""
        enabled = self._enabled_ids()
        tiles = [
            Action(ActionKind.CALENDAR_TOGGLE, data={
                "entity_id": c.entity_id, "label": c.name, "active": c.entity_id in enabled,
            })
            for c in self.calendars
        ]
        return layout_page(tiles, self.display.key_count, {0: Action(ActionKind.BACK)}, frame.page)

    def _home_key_map(self, frame: Frame) -> dict[int, Action]:
        """Home view: rooms/floors on top, special folders pinned to the bottom row."""
        content = self._items_for(frame)
        specials = [
            Action(ActionKind.OPEN_ROOM, room=self.lights_on_room),
            Action(ActionKind.OPEN_SECURITY, room=self.security_folder),
            Action(ActionKind.OPEN_CLIMATE, room=self.climate_folder),
        ]
        if self.weather is not None:
            specials.append(Action(ActionKind.OPEN_WEATHER))
        if self.calendars:
            specials.append(Action(ActionKind.OPEN_CALENDAR))
        specials.append(Action(ActionKind.CLOCK))   # live local time
        specials.append(Action(ActionKind.DATE))    # live date + weekday
        specials.append(Action(ActionKind.OPEN_SETTINGS, room=self.settings_folder))  # always last
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

    def _climate_key_map(self, frame: Frame) -> dict[int, Action]:
        """Climate view: temperature sensors, fans and thermostats, one type per column."""
        groups = self._collect_climate_groups()
        cols = getattr(self.display, "cols", 0)
        if not cols:  # no grid info: flat sequential, types still contiguous
            flat = [a for group in groups for a in group]
            return layout_page(flat, self.display.key_count, {0: Action(ActionKind.BACK)}, frame.page)
        return layout_security(groups, self.display.key_count, cols, frame.page)

    def _collect_security_groups(self) -> list[list[DeviceEntity]]:
        """Alarms, then locks, then closures, then presence — sorted, empties dropped."""
        alarms, locks, closures, presence = [], [], [], []
        for room in self.rooms:
            for entity in room.entities:
                if entity.is_alarm:
                    alarms.append(entity)
                elif entity.domain == "lock":
                    locks.append(entity)
                elif entity.is_closure:
                    closures.append(entity)
                elif entity.is_presence:
                    presence.append(entity)
        groups = [alarms, locks, closures, presence]
        for group in groups:
            group.sort(key=lambda e: e.name.lower())
        return [g for g in groups if g]

    def _collect_climate_groups(self) -> list[list[Action]]:
        """Temperature sensors (labelled by room), then fans, then thermostats.

        Temperature tiles show their room name/icon instead of the sensor's, so
        they carry the owning room; fans and thermostats are normal entity tiles.
        """
        temps: list[Action] = []
        fans: list[Action] = []
        thermostats: list[Action] = []
        for room in self.rooms:
            for entity in room.entities:
                if entity.is_temperature_sensor:
                    temps.append(Action(ActionKind.CLIMATE_TEMP, entity=entity, room=room))
                elif entity.domain == "fan":
                    fans.append(Action(ActionKind.ENTITY, entity=entity))
                elif entity.domain == "climate":
                    thermostats.append(Action(ActionKind.ENTITY, entity=entity))
        temps.sort(key=lambda a: a.room.name.lower())
        fans.sort(key=lambda a: a.entity.name.lower())
        thermostats.sort(key=lambda a: a.entity.name.lower())
        return [g for g in (temps, fans, thermostats) if g]

    def _items_for(self, frame: Frame) -> list[Action]:
        if frame.kind is FrameKind.HOME:
            # Rooms (grouped under floor headers when HA has floors). The special
            # "Lights On"/"Security" folders are added separately, pinned to the
            # bottom row by _home_key_map.
            items: list[Action] = []
            if self.floors:
                for floor in self.floors:
                    items.append(Action(ActionKind.FLOOR_HEADER, floor=floor))
                    if floor.floor_id not in self._collapsed_floors:
                        items += [Action(ActionKind.OPEN_ROOM, room=r) for r in floor.rooms]
                if self.unassigned_rooms:
                    other = Floor("__other__", "Other")
                    items.append(Action(ActionKind.FLOOR_HEADER, floor=other))
                    if other.floor_id not in self._collapsed_floors:
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
            if action.kind is ActionKind.OPEN_CALENDAR:
                self._arm_hold(key, hint=("calendar-check", "Release to pick"))
                return
            self._dispatch_down(action)
            return

        # release: only meaningful for the deferred long-press-capable keys
        if timer is not None:
            timer.cancel()
        if start is None or action is None:
            return
        if action.kind is ActionKind.OPEN_CALENDAR:
            if (time.monotonic() - start) >= LONG_PRESS_S:
                self._push(Frame(FrameKind.CALENDAR_PICKER))  # choose which calendars feed the tile
            else:
                self._open_calendar()
            return
        if action.kind is ActionKind.ENTITY and action.entity is not None:
            entity = action.entity
            long = (time.monotonic() - start) >= LONG_PRESS_S
            if long:
                self._open_entity_menu(entity)  # opens the options menu; view changes
            elif entity.is_controllable:
                self._invoke(entity)
                self._restore_key(key)  # clear any hold feedback
            else:
                self._restore_key(key)  # non-controllable short press: nothing to do

    def _arm_hold(self, key: int, entity: DeviceEntity | None = None,
                  hint: tuple[str, str] | None = None) -> None:
        """Record press time and schedule the armed-feedback render.

        ``hint`` is an (icon, text) pair for keys that aren't entity tiles; when
        omitted the wording is derived from ``entity``.
        """
        timer = threading.Timer(LONG_PRESS_S, self._show_hold_feedback, args=(key, entity, hint))
        timer.daemon = True
        with self._lock:
            self._press_start[key] = time.monotonic()
            self._hold_timers[key] = timer
        timer.start()

    def _show_hold_feedback(self, key: int, entity: DeviceEntity | None,
                            hint: tuple[str, str] | None = None) -> None:
        """Fired by the timer: if the key is still held, show it is armed.

        When the entity has a single option (History only) the hint names it;
        otherwise it announces the options menu.
        """
        with self._lock:
            if self._disconnected or key not in self._press_start:
                return  # released (or disconnected) before the threshold
            if hint is not None:
                img = self.renderer.hold_feedback(*hint)
            elif entity is None:
                return
            elif self._opens_controls(entity):
                img = self.renderer.hold_feedback("play-box", "Release for controls")
            elif self._menu_is_history_only(self._entity_menu_options(entity)):
                img = self.renderer.hold_feedback("history", "Release for history")
            else:
                img = self.renderer.hold_feedback("gesture-tap-hold", "Release for options")
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
        elif action.kind is ActionKind.OPEN_CLIMATE:
            self._push(Frame(FrameKind.CLIMATE))
        elif action.kind is ActionKind.OPEN_WEATHER:
            self._open_weather()
        elif action.kind is ActionKind.OPEN_CALENDAR:
            self._open_calendar()  # the press path arms a hold first; this is the fallback
        elif action.kind is ActionKind.CALENDAR_TOGGLE:
            self._toggle_calendar(action)
        elif action.kind is ActionKind.OPEN_SETTINGS:
            self._push(Frame(FrameKind.SETTINGS))
        elif action.kind is ActionKind.SETTINGS_ITEM:
            self._run_setting(action)
        elif action.kind in (ActionKind.WEATHER_DAY, ActionKind.WEATHER_CELL,
                             ActionKind.HISTORY_TITLE, ActionKind.HISTORY_EVENT,
                             ActionKind.TIMER_STATUS, ActionKind.CLIMATE_TEMP,
                             ActionKind.CLIMATE_STATUS, ActionKind.MEDIA_ART,
                             ActionKind.MEDIA_TEXT, ActionKind.MEDIA_VOLUME,
                             ActionKind.ALARM_STATUS, ActionKind.CLOCK, ActionKind.DATE,
                             ActionKind.CALENDAR_DAY, ActionKind.CALENDAR_EVENT):
            return  # forecast/history/status/media-info/clock tiles are not interactive
        elif action.kind is ActionKind.MENU_ITEM:
            self._dispatch_menu_target(action.entity, (action.data or {}).get("target"))
        elif action.kind is ActionKind.PICKER_CELL:
            self._apply_picker_cell(action)
        elif action.kind is ActionKind.SERVICE_BUTTON:
            self._apply_service_button(action)
        elif action.kind is ActionKind.CLIMATE_ADJUST:
            self._adjust_climate_temp(action)
        elif action.kind is ActionKind.CLIMATE_POWER:
            self._send(action.entity.climate_power_call())
        elif action.kind is ActionKind.FLOOR_HEADER:
            self._toggle_floor(action.floor)
        elif action.kind is ActionKind.BACK:
            self._pop()
        elif action.kind is ActionKind.PAGE:
            self._change_page(action.delta)
        elif action.kind is ActionKind.TIMER_ACTION:
            self._invoke_timer(action)

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

    def _toggle_floor(self, floor: Floor) -> None:
        """Collapse/expand a floor's rooms on the home screen."""
        with self._lock:
            fid = floor.floor_id
            if fid in self._collapsed_floors:
                self._collapsed_floors.discard(fid)
            else:
                self._collapsed_floors.add(fid)
        self.render()

    def _render_room_tile(self, room: Room):
        """Render a real room folder with its live indicator dots."""
        light_on = any(e.domain == "light" and e.status is Status.ON for e in room.entities)
        presence = any(e.is_presence and e.status is Status.ON for e in room.entities)
        return self.renderer.room(room, accent=renderer_mod.ROOM_ACCENT, light_on=light_on, presence=presence)

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

    def _menu_is_history_only(self, options: list[Action]) -> bool:
        """True when the menu offers nothing beyond Toggle and/or History — in
        that case the long press goes straight to the history view."""
        return {o.data["target"] for o in options} <= {"toggle", "history"}

    @staticmethod
    def _opens_controls(entity: DeviceEntity) -> bool:
        """Entities that open a combined control view directly on long press
        (rather than the button-menu used by lights/locks)."""
        return (entity.is_media_player or entity.is_alarm or entity.is_climate
                or entity.is_timer or entity.domain == "cover"
                or (entity.domain == "fan" and entity.supports_fan_speed))

    def _open_entity_menu(self, entity: DeviceEntity) -> None:
        """Open the long press view. Most controllable types open a combined
        control view directly; lights/locks open the button-menu; entities whose
        only options are Toggle/History go straight to history (single press
        still toggles)."""
        if entity.is_media_player:
            self._push(Frame(FrameKind.MEDIA, entity=entity))
        elif entity.is_alarm:
            self._push(Frame(FrameKind.ALARM, entity=entity))
        elif entity.is_climate:
            self._open_climate_detail(entity)
        elif entity.is_timer:
            self._open_timer(entity)
        elif entity.domain == "cover":
            self._push(Frame(FrameKind.COVER_ACTIONS, entity=entity))
        elif entity.domain == "fan" and entity.supports_fan_speed:
            self._push(Frame(FrameKind.FAN, entity=entity))
        elif self._menu_is_history_only(self._entity_menu_options(entity)):
            self._open_history(entity)
        else:
            self._push(Frame(FrameKind.ENTITY_MENU, entity=entity))

    def _dispatch_menu_target(self, entity: DeviceEntity, target: str | None) -> None:
        """Act on a chosen menu option: open a sub-view or fire an action."""
        if target == "toggle":
            self._invoke(entity)  # same as a single press; stays in the view (tile updates live)
        elif target == "history":
            self._open_history(entity)
        elif target == "brightness":
            self._push(Frame(FrameKind.PICKER, entity=entity, data={"type": "brightness"}))
        elif target == "color":
            self._push(Frame(FrameKind.PICKER, entity=entity, data={"type": "color"}))
        elif target == "temperature":
            self._push(Frame(FrameKind.PICKER, entity=entity, data={"type": "temperature"}))
        elif target == "fan_speed":  # only reached for a fan with no presets
            self._push(Frame(FrameKind.PICKER, entity=entity, data={"type": "fan_percentage"}))
        elif target == "position":
            self._push(Frame(FrameKind.PICKER, entity=entity, data={"type": "cover_position"}))
        elif target == "lock_open":
            self._send(entity.long_press_call())
            if self.stack[-1].kind is FrameKind.ENTITY_MENU:
                self._pop()  # close the menu, back to the entity's view

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

    def _open_calendar(self) -> None:
        """Open the agenda: upcoming events across the chosen calendars.

        Fetched on entry like the forecast. If the service isn't available the
        view falls back to each calendar's current-or-next event, which we
        already know from its attributes.
        """
        active = self._active_calendars()
        now = datetime.now(self.tz)
        raw: dict[str, list[dict]] = {}
        if self.on_calendar_events is not None and active:
            try:
                raw = self.on_calendar_events([c.entity_id for c in active], self.agenda_days) or {}
            except Exception as exc:  # noqa: BLE001 - the agenda is best-effort
                logger.warning("Calendar events fetch failed: %s", exc)
        names = {c.entity_id: c.name for c in self.calendars}
        events = parse_events(raw, names, now, self.tz)
        if not events:
            events = events_from_calendars(active, now, self.tz)
        self._push(Frame(FrameKind.CALENDAR, events=events))

    def _toggle_calendar(self, action: Action) -> None:
        """Turn a calendar on/off in the picker and persist the choice.

        Stays in the picker so several can be toggled in a row.
        """
        entity_id = (action.data or {}).get("entity_id")
        if not entity_id:
            return
        with self._lock:
            ids = self._enabled_ids()
            if entity_id in ids:
                ids.discard(entity_id)
            else:
                ids.add(entity_id)
            self._enabled_calendars = ids
            self._calendar_shown = ""  # force the home tile to repaint on the way back
        state.save({**state.load(), "calendars": sorted(ids)})
        self.render()

    def _open_history(self, entity: DeviceEntity) -> None:
        raw: list[dict] = []
        if self.on_logbook is not None:
            try:
                raw = self.on_logbook(entity.entity_id)
            except Exception as exc:  # noqa: BLE001 - history is best-effort
                logger.warning("Logbook fetch failed for %s: %s", entity.entity_id, exc)
        self._push(Frame(FrameKind.HISTORY, entity=entity, history=parse_logbook(raw, self.tz)))

    def _open_timer(self, entity: DeviceEntity) -> None:
        self._push(Frame(FrameKind.TIMER, entity=entity))

    def _open_climate_detail(self, entity: DeviceEntity) -> None:
        self._push(Frame(FrameKind.CLIMATE_DETAIL, entity=entity))

    def _adjust_climate_temp(self, action: Action) -> None:
        """Nudge a thermostat's target set-point by a whole degree (± via action.delta)."""
        entity = action.entity
        target = entity.target_temperature
        if target is None:  # nothing to nudge (e.g. a range-only thermostat)
            return
        self._send(entity.climate_set_temperature_call(target + action.delta))

    def _send(self, call) -> None:
        """Fire a service call, swallowing errors so a bad call can't kill the deck thread."""
        if call is None:
            return
        try:
            self.on_service(call)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Service call %s failed: %s", call, exc)

    def _run_setting(self, action: Action) -> None:
        which = (action.data or {}).get("action")
        callback = {"reload": self.on_reload, "rotate": self.on_rotate,
                    "brightness": self.on_brightness}.get(which)
        if callback is None:
            return
        try:
            callback()  # reload re-fetches from HA; rotate re-orients + redraws
        except Exception as exc:  # noqa: BLE001 - a failed setting must not crash the deck
            logger.warning("Setting '%s' failed: %s", which, exc)

    def set_model(self, rooms, floors, unassigned_rooms, weather, calendars=None) -> None:
        """Swap in a freshly loaded model (used by the Settings reload)."""
        with self._lock:
            self.rooms = rooms
            self.floors = floors or []
            self.unassigned_rooms = unassigned_rooms or []
            self.weather = weather
            self.calendars = calendars or []
            self._calendar_shown = ""
            self._collapsed_floors.clear()  # floor ids may have changed
            # _enabled_calendars is the user's choice, keyed by entity id: it survives a reload.
        self.home()  # return to a freshly rendered home

    def _invoke_timer(self, action: Action) -> None:
        d = action.data or {}
        call = ("timer", d.get("service"), action.entity.entity_id, {})
        try:
            self.on_service(call)  # the resulting state change re-renders the view
        except Exception as exc:  # noqa: BLE001 - a bad call must not kill the deck thread
            logger.warning("Timer service %s failed: %s", call, exc)

    def update_weather(self, state: str, attributes: dict) -> None:
        """Refresh the weather entity and re-render its home button if visible."""
        with self._lock:
            if self.weather is None or self._disconnected:
                return
            self.weather.update(state, attributes)
            for key, action in self.key_map.items():
                if action.kind is ActionKind.OPEN_WEATHER:
                    # Keep the reserved-band background (matches the initial render);
                    # omitting bg here repainted the tile with the default dark BG.
                    self.display.set_image(key, self.renderer.weather_button(self.weather, bg=renderer_mod.RESERVED_BG))
                    return

    def update_calendar(self, entity_id: str, state_value: str, attributes: dict) -> bool:
        """Refresh a calendar entity and repaint the home tile if it's visible.

        Returns False for an entity we don't track, so the caller can fall
        through to the normal entity path.
        """
        with self._lock:
            calendar = next((c for c in self.calendars if c.entity_id == entity_id), None)
            if calendar is None:
                return False
            calendar.update(state_value, attributes, self.tz)
            if self._disconnected or self.stack[-1].kind is not FrameKind.HOME:
                return True
            for key, action in self.key_map.items():
                if action.kind is ActionKind.OPEN_CALENDAR:
                    event = self._next_event()
                    self._calendar_shown = self._calendar_text(event)
                    self.display.set_image(
                        key, self.renderer.calendar_button(event, bg=renderer_mod.RESERVED_BG))
                    break
            return True

    def _apply_picker_cell(self, action: Action) -> None:
        """Apply a picker swatch (brightness/color/temp/percentage) and close it."""
        self._send((action.data or {}).get("call"))
        self._pop()  # apply and close the picker

    def _apply_service_button(self, action: Action) -> None:
        """Fire a preset/cover button; close the view only if it asked to."""
        d = action.data or {}
        self._send(d.get("call"))
        if d.get("close"):
            self._pop()

    def _invoke(self, entity: DeviceEntity) -> None:
        """Fire an entity's single-press service call (toggle / lock / press / …)."""
        self._send(entity.service_call())

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
            # Detail views that reflect live state rebuild when their entity
            # changes (timer remaining, target temp, active preset, toggle state).
            viewing_detail = (
                frame.kind in (FrameKind.TIMER, FrameKind.CLIMATE_DETAIL, FrameKind.ENTITY_MENU,
                               FrameKind.MEDIA, FrameKind.ALARM, FrameKind.FAN)
                and frame.entity is not None
                and frame.entity.entity_id == entity_id
            )
        if viewing_dynamic or viewing_detail:
            self.render()  # rebuild the view with the new state
            return
        with self._lock:
            if frame.kind is FrameKind.HOME:
                # A light/presence change updates the indicator dots on its room tile.
                for key, action in self.key_map.items():
                    if action.kind is ActionKind.OPEN_ROOM and not action.room.is_dynamic:
                        member = next((e for e in action.room.entities if e.entity_id == entity_id), None)
                        if member is not None and (member.domain == "light" or member.is_presence):
                            self.display.set_image(key, self._render_room_tile(action.room))
                return
            for key, action in self.key_map.items():
                if action.entity is None or action.entity.entity_id != entity_id:
                    continue
                if action.kind in (ActionKind.ENTITY, ActionKind.CLIMATE_TEMP):
                    self.display.set_image(key, self._image_for(action))
                    return

    def tick(self) -> None:
        """Re-render live tiles (active timers, and the home clock/date).

        Called ~once a second by a background ticker. Active timers on the
        current view count down; the home-screen clock/date tiles are redrawn
        only when their displayed text actually changes (so idle minutes cost no
        USB writes). A no-op when nothing needs updating.
        """
        with self._lock:
            if self._disconnected:
                return
            frame = self.stack[-1]
            if frame.kind is FrameKind.ROOM:
                for key, action in self.key_map.items():
                    entity = action.entity
                    if (action.kind is ActionKind.ENTITY and entity is not None
                            and entity.is_timer and (entity.state or "").lower() == "active"):
                        self.display.set_image(key, self.renderer.device(entity))
            elif frame.kind is FrameKind.TIMER:
                entity = frame.entity
                if entity is not None and (entity.state or "").lower() == "active":
                    for key, action in self.key_map.items():
                        if action.kind is ActionKind.TIMER_STATUS:
                            self.display.set_image(key, self.renderer.timer_status(entity))
            elif frame.kind is FrameKind.HOME:
                self._tick_clock()

    @staticmethod
    def _calendar_text(event: CalendarEvent | None) -> str:
        """The calendar tile's rendered text, used to detect when it changed."""
        return "" if event is None else f"{event.time_label}|{event.summary}"

    def _tick_clock(self) -> None:
        """Redraw the home clock/date/calendar tiles when their text has changed."""
        now = datetime.now(self.tz)
        clock, date = now.strftime("%H:%M"), now.strftime("%a %b %d")
        event = self._next_event() if self.calendars else None
        calendar = self._calendar_text(event)
        for key, action in self.key_map.items():
            if action.kind is ActionKind.CLOCK and clock != self._clock_shown:
                self.display.set_image(key, self.renderer.clock_tile(now))
            elif action.kind is ActionKind.DATE and date != self._date_shown:
                self.display.set_image(key, self.renderer.date_tile(now))
            elif action.kind is ActionKind.OPEN_CALENDAR and calendar != self._calendar_shown:
                self.display.set_image(
                    key, self.renderer.calendar_button(event, bg=renderer_mod.RESERVED_BG))
        self._clock_shown, self._date_shown, self._calendar_shown = clock, date, calendar

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


def _spread(lo: int, hi: int, n: int) -> list[int]:
    """``n`` values evenly spaced from ``lo`` to ``hi`` (inclusive), low→high."""
    if n <= 1:
        return [hi]
    return [round(lo + (hi - lo) * i / (n - 1)) for i in range(n)]


def _hues(n: int) -> list[int]:
    """``n`` hues evenly spread around the color wheel (0→360°)."""
    return [round(i * 360 / n) for i in range(max(1, n))]


def layout_home(
    content: list[Action],
    specials: list[Action],
    total_keys: int,
    cols: int,
    page: int,
) -> dict[int, Action]:
    """Home layout: rooms/floors on top, special folders in a contrasted band.

    The band is the bottom row in landscape and the bottom two rows in portrait
    (taller-than-wide). Its cells get a contrasted background (RESERVED_BLANK)
    so the special zone is visually distinct; the specials are bottom-anchored
    within it. Room/floor content fills the rows above and paginates there.
    """
    rows = total_keys // cols
    reserved_rows = 2 if rows > cols else 1                       # portrait: 2 rows
    special_rows = max(1, _ceil_div(len(specials), cols))
    reserved_rows = max(reserved_rows, special_rows)
    zone_start = (rows - reserved_rows) * cols                    # first key of the band
    special_start = (rows - special_rows) * cols                  # specials pinned to the bottom
    content_capacity = zone_start

    if content_capacity <= 0:  # deck too short for a reserved band
        return layout_page(content + specials, total_keys, {}, page)

    result: dict[int, Action] = {}
    for key in range(zone_start, total_keys):                     # contrasted band background
        result[key] = Action(ActionKind.RESERVED_BLANK)
    for i, action in enumerate(specials):                         # specials over the band
        result[special_start + i] = action

    page_count = max(1, _ceil_div(len(content), content_capacity))
    page = max(0, min(page, page_count - 1))
    for key, action in enumerate(content[page * content_capacity : (page + 1) * content_capacity]):
        result[key] = action

    if page_count > 1:  # pagination goes in band cells not used by the specials
        used = set(range(special_start, special_start + len(specials)))
        free = [k for k in range(zone_start, total_keys) if k not in used]
        if page > 0 and len(free) >= 2:
            result[free[-2]] = Action(ActionKind.PAGE, delta=-1)
        if page < page_count - 1 and free:
            result[free[-1]] = Action(ActionKind.PAGE, delta=1)
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


def layout_calendar(
    days: list[CalendarDay],
    total_keys: int,
    cols: int,
    page: int,
) -> dict[int, Action]:
    """Lay out the agenda: Back in column 0, one day per column.

    Each day gets a header tile (weekday + date) on the top row with its events
    stacked underneath, so a column reads as a day. A day with more events than
    fit spills into the next column, repeating the header. Column 0 holds Back
    (and Prev/Next when there are more days than columns). Falls back to a flat
    sequential layout on decks too small for the banded layout.
    """
    back: dict[int, Action] = {0: Action(ActionKind.BACK)}
    rows = total_keys // cols
    content_cols = cols - 1  # column 0 is reserved for Back/navigation
    per_col = rows - 1       # the top cell of each column is the day header

    def event_tile(event, today: bool) -> Action:
        return Action(ActionKind.CALENDAR_EVENT, event=event, data={"today": today})

    if per_col < 1 or content_cols < 1:  # too small for headers + a nav column
        flat = [event_tile(e, i == 0) for i, day in enumerate(days) for e in day.events]
        return layout_page(flat, total_keys, back, page)

    # One "column" per chunk of `per_col` events; a busy day spans several, and
    # a day with nothing on still gets its (empty) column.
    columns: list[tuple[CalendarDay, list[Action]]] = []
    for i, day in enumerate(days):
        starts = list(range(0, len(day.events), per_col)) or [0]  # [0] = an empty day
        for start in starts:
            chunk = [event_tile(e, i == 0) for e in day.events[start : start + per_col]]
            columns.append((day, chunk))

    result = dict(back)
    page_count = max(1, _ceil_div(len(columns), content_cols))
    page = max(0, min(page, page_count - 1))

    if page_count > 1:  # navigation tucked into the bottom of the (empty) column 0
        if page > 0:
            result[(rows - 2) * cols] = Action(ActionKind.PAGE, delta=-1)
        if page < page_count - 1:
            result[(rows - 1) * cols] = Action(ActionKind.PAGE, delta=1)

    for col_offset, (day, chunk) in enumerate(columns[page * content_cols : (page + 1) * content_cols]):
        col = 1 + col_offset
        result[col] = Action(ActionKind.CALENDAR_DAY,
                             data={"label": day.label, "date": day.date_label})
        for row, tile in enumerate(chunk):
            result[(row + 1) * cols + col] = tile
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
