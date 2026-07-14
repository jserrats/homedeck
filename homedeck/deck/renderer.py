"""Render Stream Deck key images with Pillow.

Produces plain RGB ``PIL.Image`` objects at the key's pixel size; converting to
the deck's native format is the controller's job, which keeps the renderer
usable without any hardware attached (e.g. for ``--export``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import icons
from ..color import hs_to_rgb, kelvin_to_rgb, scale
from ..ha.history import HistoryEvent
from ..ha.model import BUTTON_DOMAINS, CLIMATE_DOMAINS, DeviceEntity, Floor, Room, Status, _format_number
from ..ha.weather import ForecastDay, Weather

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
LABEL_FONT = ASSETS_DIR / "DejaVuSans.ttf"
VALUE_FONT = ASSETS_DIR / "DejaVuSans-Bold.ttf"

# Palette
BG = (16, 16, 18)
RESERVED_BG = (36, 36, 44)  # slightly lighter band behind the special folders
TEXT = (236, 236, 238)
ACCENT = (255, 176, 0)       # on (lights/switches/...)
NEUTRAL = (120, 120, 126)    # off / informational
UNAVAILABLE = (208, 64, 52)  # unavailable / error
SECURE = (34, 197, 94)       # locked, or a closed door/window (green)
OPEN = (249, 115, 22)        # an open door/window/closure (orange)
PENDING = (250, 204, 21)     # transitional, e.g. locking/unlocking (yellow)
UNAVAILABLE_ICON = (96, 96, 102)  # dim grey icon for unavailable devices
WARNING = (239, 68, 68)      # red warning-triangle badge
ROOM_ACCENT = (96, 165, 250)     # room folders
FLOOR_ACCENT = (52, 211, 153)    # floor folders
LIGHTS_ACCENT = (255, 176, 0)    # "Lights On" folder
SECURITY_ACCENT = (168, 85, 247)  # "Security" folder (purple)
CLIMATE_ACCENT = (45, 212, 191)   # "Climate" folder (teal)
WEATHER_ACCENT = (125, 200, 247)  # weather button / forecast (sky blue)
SETTINGS_ACCENT = (156, 163, 175)  # "Settings" folder (slate grey)
CLIMATE_ICON = (125, 200, 247)    # active fan / climate icon (sky blue)
NAV_COLOR = (210, 210, 214)
DOT_LIGHT = (255, 210, 0)        # room indicator: a light is on (yellow)
DOT_PRESENCE = (168, 85, 247)    # room indicator: presence detected (purple)

# Icons for common HA climate preset modes (falls back to a generic tuner).
PRESET_ICONS = {
    "eco": "leaf",
    "away": "home-export-outline",
    "home": "home",
    "comfort": "sofa",
    "sleep": "power-sleep",
    "boost": "rocket-launch-outline",
    "activity": "run",
    "none": "tune",
}

STATUS_COLORS = {
    Status.ON: ACCENT,
    Status.OFF: NEUTRAL,
    Status.UNAVAILABLE: UNAVAILABLE,
    Status.SECURE: SECURE,
    Status.OPEN: OPEN,
    Status.PENDING: PENDING,
}


class KeyRenderer:
    def __init__(self, key_size: tuple[int, int] = (96, 96)) -> None:
        self.w, self.h = key_size

    # -- font caching -------------------------------------------------------

    @staticmethod
    @lru_cache(maxsize=32)
    def _font(path_str: str, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(path_str, size)

    def _label_font(self, size: int) -> ImageFont.FreeTypeFont:
        return self._font(str(LABEL_FONT), size)

    def _value_font(self, size: int) -> ImageFont.FreeTypeFont:
        return self._font(str(VALUE_FONT), size)

    def _fit_value_font(self, text: str, max_size: int, max_width: int, min_size: int = 9) -> ImageFont.FreeTypeFont:
        """Largest value font (<= max_size) whose text fits within max_width.

        Prevents long readings like "12345.68 kWh" from being clipped.
        """
        size = max_size
        while size > min_size:
            font = self._value_font(size)
            if font.getlength(text) <= max_width:
                return font
            size -= 1
        return self._value_font(min_size)

    def _icon_font(self, size: int) -> ImageFont.FreeTypeFont:
        return self._font(str(icons.font_path()), size)

    # -- primitives ---------------------------------------------------------

    def _canvas(self) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        img = Image.new("RGB", (self.w, self.h), BG)
        return img, ImageDraw.Draw(img)

    def _draw_glyph(self, draw, glyph: str, size: int, cy: int, color, stroke_width: int = 0, stroke_fill=None) -> None:
        font = self._icon_font(size)
        draw.text((self.w / 2, cy), glyph, font=font, fill=color, anchor="mm",
                  stroke_width=stroke_width, stroke_fill=stroke_fill)

    def _draw_label(self, draw, text: str, *, y: int, size: int, color=TEXT, max_lines: int = 2) -> None:
        font = self._label_font(size)
        lines = _wrap(text, font, self.w - 6, max_lines)
        line_h = size + 2
        start_y = y
        for i, line in enumerate(lines):
            draw.text((self.w / 2, start_y + i * line_h), line, font=font, fill=color, anchor="ma")

    # -- key types ----------------------------------------------------------

    def _icon_color_for(self, entity: DeviceEntity) -> tuple[int, int, int]:
        """Status-reflecting icon tint: dim grey (unavailable), purple (presence),
        sky blue (active fan/climate), a light's own color, else the status palette."""
        if entity.status is Status.UNAVAILABLE:
            return UNAVAILABLE_ICON
        if entity.is_presence and entity.status is Status.ON:
            return DOT_PRESENCE  # presence detected -> purple (matches the room dot)
        if entity.domain in CLIMATE_DOMAINS and entity.status is Status.ON:
            return CLIMATE_ICON  # active fan / climate -> sky blue
        return entity.icon_color() or STATUS_COLORS[entity.status]

    def device(self, entity: DeviceEntity) -> Image.Image:
        img, draw = self._canvas()
        unavailable = entity.status is Status.UNAVAILABLE
        # Unavailable devices keep a readable dim icon and get a warning badge
        # instead of being painted red. A color/temp/dimmable light that is on
        # tints its icon with its actual color; otherwise the status palette.
        color = self._icon_color_for(entity)
        icon_name = icons.resolve_icon_name(
            entity.domain, entity.device_class, entity.explicit_icon,
            state=entity.state, is_open=entity.closure_open(),
        )
        glyph = icons.glyph(icon_name)
        value = entity.display_value()

        if value is not None:
            # Read-only entity: small icon up top, big value, name at the bottom.
            self._draw_glyph(draw, glyph, size=int(self.h * 0.28), cy=int(self.h * 0.22), color=color)
            value_font = self._fit_value_font(value, max_size=int(self.h * 0.24), max_width=self.w - 8)
            draw.text((self.w / 2, self.h * 0.52), value, font=value_font, fill=TEXT, anchor="mm")
            self._draw_label(draw, entity.name, y=int(self.h * 0.74), size=11, color=NAV_COLOR, max_lines=1)
        elif entity.domain in BUTTON_DOMAINS and not unavailable:
            # Buttons: black icon with a white outline (looks like a pressable key).
            self._draw_glyph(draw, glyph, size=int(self.h * 0.46), cy=int(self.h * 0.38),
                             color=(0, 0, 0), stroke_width=max(2, int(self.h * 0.03)), stroke_fill=(255, 255, 255))
            self._draw_label(draw, entity.name, y=int(self.h * 0.66), size=13)
        else:
            # Controllable: large colored icon, name at the bottom.
            self._draw_glyph(draw, glyph, size=int(self.h * 0.46), cy=int(self.h * 0.38), color=color)
            self._draw_label(draw, entity.name, y=int(self.h * 0.66), size=13)

        if unavailable:
            self._draw_warning_badge(draw)
        elif entity.is_off:
            self._draw_off_bar(draw)
        return img

    def _draw_off_bar(self, draw) -> None:
        """A grey diagonal bar across the icon marking an off device."""
        p0 = (self.w * 0.24, self.h * 0.60)
        p1 = (self.w * 0.76, self.h * 0.14)
        # Dark underlay first so the grey bar reads over light and dark icons alike.
        draw.line([p0, p1], fill=(18, 18, 20), width=max(3, int(self.h * 0.11)))
        draw.line([p0, p1], fill=(188, 188, 194), width=max(2, int(self.h * 0.055)))

    def _draw_warning_badge(self, draw) -> None:
        """A small red warning triangle in the top-right corner."""
        font = self._icon_font(int(self.h * 0.32))
        draw.text((self.w - 2, 1), icons.glyph("alert"), font=font, fill=WARNING, anchor="rt")

    def climate_room_reading(self, entity: DeviceEntity, room: Room) -> Image.Image:
        """A temperature tile in the Climate folder, labelled by its room.

        Shows the room's icon (not the thermometer) and the room's name (not the
        entity id), with the sensor's current reading in the middle.
        """
        img, draw = self._canvas()
        color = UNAVAILABLE_ICON if entity.status is Status.UNAVAILABLE else STATUS_COLORS.get(entity.status, NEUTRAL)
        icon_name = icons.resolve_icon_name("", None, room.icon)
        if not icon_name or icon_name == icons.GENERIC_FALLBACK:
            icon_name = "home-thermometer"  # room has no icon: a room-ish climate default
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.28), cy=int(self.h * 0.22), color=color)
        value = entity.display_value() or "—"
        value_font = self._fit_value_font(value, max_size=int(self.h * 0.24), max_width=self.w - 8)
        draw.text((self.w / 2, self.h * 0.52), value, font=value_font, fill=TEXT, anchor="mm")
        self._draw_label(draw, room.name, y=int(self.h * 0.74), size=11, color=NAV_COLOR, max_lines=1)
        return img

    def _media_state_glyph(self, entity: DeviceEntity) -> str | None:
        """Play/pause badge matching state: 'pause' while playing, 'play' when
        paused (i.e. the action a press performs); no badge when idle/off."""
        if entity.is_playing:
            return "pause"
        if (entity.state or "").lower() == "paused":
            return "play"
        return None

    def media_device(self, entity: DeviceEntity, art: Image.Image | None = None) -> Image.Image:
        """A media player's room tile. With artwork it's the miniature (cover-fit)
        with the title on a dark strip and a play/pause badge; otherwise a normal
        status-colored icon + name."""
        if art is not None:
            img = ImageOps.fit(art.convert("RGB"), (self.w, self.h))
            draw = ImageDraw.Draw(img)
            strip = int(self.h * 0.30)
            draw.rectangle([0, self.h - strip, self.w, self.h], fill=(0, 0, 0))
            self._draw_label(draw, entity.media_title or entity.name, y=self.h - strip + 2,
                             size=11, color=TEXT, max_lines=1)
            glyph = self._media_state_glyph(entity)
            if glyph:
                draw.text((3, 1), icons.glyph(glyph), font=self._icon_font(int(self.h * 0.26)),
                          fill=(255, 255, 255), anchor="lt")
            return img
        img, draw = self._canvas()
        icon_name = icons.resolve_icon_name(entity.domain, entity.device_class, entity.explicit_icon,
                                            state=entity.state)
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.46), cy=int(self.h * 0.38),
                         color=self._icon_color_for(entity))
        self._draw_label(draw, entity.name, y=int(self.h * 0.66), size=13)
        return img

    def media_art_tile(self, entity: DeviceEntity, art: Image.Image | None, cell: tuple[int, int]) -> Image.Image:
        """One cell ``(row, col)`` of the 3x3 album-art mosaic.

        With artwork the full image is cover-fit to a 3x3 canvas and this cell's
        crop returned, so the nine tiles reassemble into one picture. With no
        artwork the center cell shows the player's icon + name; the rest are blank.
        """
        r, c = cell
        if art is not None:
            mosaic = ImageOps.fit(art.convert("RGB"), (self.w * 3, self.h * 3))
            return mosaic.crop((c * self.w, r * self.h, (c + 1) * self.w, (r + 1) * self.h))
        img, draw = self._canvas()
        if (r, c) == (1, 1):
            icon_name = icons.resolve_icon_name(entity.domain, entity.device_class, entity.explicit_icon,
                                                state=entity.state)
            self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.5), cy=int(self.h * 0.40),
                             color=self._icon_color_for(entity))
            self._draw_label(draw, entity.name, y=int(self.h * 0.72), size=11, max_lines=1)
        return img

    def media_meta(self, caption: str, value: str | None) -> Image.Image:
        """A metadata tile shown below the artwork: a small caption over the
        (wrapped) song title / artist / album."""
        img, draw = self._canvas()
        self._draw_label(draw, caption, y=int(self.h * 0.06), size=10, color=NAV_COLOR, max_lines=1)
        self._draw_label(draw, value or "—", y=int(self.h * 0.30), size=12, color=TEXT, max_lines=3)
        return img

    def media_volume(self, entity: DeviceEntity) -> Image.Image:
        """A volume readout tile: level percent (or 'Muted') under a speaker icon."""
        img, draw = self._canvas()
        muted = entity.is_muted
        icon = "volume-off" if muted else "volume-high"
        self._draw_glyph(draw, icons.glyph(icon), size=int(self.h * 0.30), cy=int(self.h * 0.26),
                         color=UNAVAILABLE if muted else CLIMATE_ICON)
        pct = entity.volume_pct
        text = "Muted" if muted else (f"{pct}%" if pct is not None else "—")
        draw.text((self.w / 2, self.h * 0.58), text, font=self._value_font(int(self.h * 0.24)), fill=TEXT, anchor="mm")
        self._draw_label(draw, "Volume", y=int(self.h * 0.78), size=10, color=NAV_COLOR, max_lines=1)
        return img

    def reserved_blank(self) -> Image.Image:
        """A solid tile in the special-folder band's contrasted background."""
        return Image.new("RGB", (self.w, self.h), RESERVED_BG)

    def room(
        self,
        room: Room,
        accent: tuple[int, int, int] = ROOM_ACCENT,
        light_on: bool = False,
        presence: bool = False,
        bg: tuple[int, int, int] = BG,
    ) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        icon_name = icons.resolve_icon_name("", None, room.icon) or "door"
        if icon_name == icons.GENERIC_FALLBACK:
            icon_name = "door"  # nicer default for a room/folder than a question mark
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.42), cy=int(self.h * 0.36), color=accent)
        self._draw_label(draw, room.name, y=int(self.h * 0.64), size=13)

        dots = ([DOT_LIGHT] if light_on else []) + ([DOT_PRESENCE] if presence else [])
        self._draw_indicator_dots(draw, dots)
        return img

    def _draw_indicator_dots(self, draw, colors: list[tuple[int, int, int]]) -> None:
        """Status dots stacked down the top-left of a tile."""
        if not colors:
            return
        r = max(4, int(self.h * 0.072))
        cx, cy = int(self.w * 0.16), int(self.h * 0.16)
        step = int(r * 2 + self.h * 0.06)  # diameter + a slight gap
        for i, color in enumerate(colors):
            y = cy + i * step
            draw.ellipse([cx - r - 1, y - r - 1, cx + r + 1, y + r + 1], fill=(10, 10, 12))  # outline
            draw.ellipse([cx - r, y - r, cx + r, y + r], fill=color)

    def floor_header(self, floor: Floor, collapsed: bool = False) -> Image.Image:
        """A tappable section label for a floor; a chevron shows collapsed/expanded."""
        bg = (18, 60, 48)  # dark teal so the floor name reads in light text
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        icon_name = icons.resolve_icon_name("", None, floor.icon) or "floor-plan"
        if icon_name == icons.GENERIC_FALLBACK:
            icon_name = "floor-plan"
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.34), cy=int(self.h * 0.32), color=FLOOR_ACCENT)
        self._draw_label(draw, floor.name, y=int(self.h * 0.58), size=13, color=(220, 245, 238))
        # Chevron: right = collapsed, down = expanded.
        chevron = icons.glyph("chevron-right" if collapsed else "chevron-down")
        draw.text((self.w - 3, 2), chevron, font=self._icon_font(int(self.h * 0.24)), fill=(200, 230, 222), anchor="rt")
        return img

    def weather_button(self, weather: Weather, bg: tuple[int, int, int] = BG) -> Image.Image:
        """Home-screen weather tile: condition icon + outside temperature."""
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        self._draw_glyph(draw, icons.glyph(weather.icon), size=int(self.h * 0.30), cy=int(self.h * 0.24), color=WEATHER_ACCENT)
        draw.text((self.w / 2, self.h * 0.55), weather.temp_text(), font=self._value_font(int(self.h * 0.26)), fill=TEXT, anchor="mm")
        self._draw_label(draw, "Weather", y=int(self.h * 0.76), size=11, color=NAV_COLOR, max_lines=1)
        return img

    def weather_day(self, day: ForecastDay) -> Image.Image:
        """Compact forecast tile (small-deck fallback): weekday, icon, high/low."""
        img, draw = self._canvas()
        if day.label:
            self._draw_label(draw, day.label, y=int(self.h * 0.06), size=12, color=NAV_COLOR, max_lines=1)
        self._draw_glyph(draw, icons.glyph(day.icon), size=int(self.h * 0.34), cy=int(self.h * 0.44), color=WEATHER_ACCENT)
        draw.text((self.w / 2, self.h * 0.82), day.temp_text(), font=self._value_font(int(self.h * 0.185)), fill=TEXT, anchor="mm")
        return img

    # Individual forecast cells for the full-matrix layout (one column per day).
    def weather_label_cell(self, day: ForecastDay) -> Image.Image:
        img, draw = self._canvas()
        draw.text((self.w / 2, self.h / 2), day.label or "—", font=self._value_font(int(self.h * 0.34)), fill=WEATHER_ACCENT, anchor="mm")
        return img

    def weather_icon_cell(self, day: ForecastDay) -> Image.Image:
        img, draw = self._canvas()
        self._draw_glyph(draw, icons.glyph(day.icon), size=int(self.h * 0.6), cy=int(self.h * 0.5), color=WEATHER_ACCENT)
        return img

    def weather_temp_cell(self, value: str, caption: str) -> Image.Image:
        img, draw = self._canvas()
        self._draw_label(draw, caption, y=int(self.h * 0.12), size=11, color=NAV_COLOR, max_lines=1)
        draw.text((self.w / 2, self.h * 0.58), value, font=self._value_font(int(self.h * 0.34)), fill=TEXT, anchor="mm")
        return img

    def history_title(self, entity: DeviceEntity) -> Image.Image:
        """Header tile for the history view: entity name under a clock icon."""
        img, draw = self._canvas()
        self._draw_glyph(draw, icons.glyph("history"), size=int(self.h * 0.34), cy=int(self.h * 0.30), color=WEATHER_ACCENT)
        self._draw_label(draw, entity.name, y=int(self.h * 0.56), size=12, max_lines=2)
        return img

    def history_event(self, event: HistoryEvent) -> Image.Image:
        """One timeline entry: clock time + relative time, the new state, and its trigger."""
        img, draw = self._canvas()
        self._draw_label(draw, event.time_label, y=int(self.h * 0.04), size=12, color=TEXT, max_lines=1)
        self._draw_label(draw, event.rel_label, y=int(self.h * 0.25), size=10, color=NAV_COLOR, max_lines=1)
        state_color = ACCENT if event.state.lower() in ("on", "open", "home") else NEUTRAL
        draw.text((self.w / 2, self.h * 0.52), event.state, font=self._value_font(int(self.h * 0.19)),
                  fill=state_color, anchor="mm")
        if event.trigger:
            self._draw_label(draw, event.trigger, y=int(self.h * 0.72), size=10, color=(190, 190, 195), max_lines=1)
        return img

    def timer_status(self, entity: DeviceEntity) -> Image.Image:
        """Big remaining-time display for the timer detail view."""
        img, draw = self._canvas()
        color = STATUS_COLORS.get(entity.status, NEUTRAL)
        self._draw_label(draw, entity.state.upper(), y=int(self.h * 0.08), size=11, color=color, max_lines=1)
        draw.text((self.w / 2, self.h * 0.45), entity.display_value() or "—",
                  font=self._value_font(int(self.h * 0.26)), fill=TEXT, anchor="mm")
        self._draw_label(draw, entity.name, y=int(self.h * 0.70), size=11, color=NAV_COLOR, max_lines=2)
        return img

    def action_button(self, icon_name: str, label: str, color: tuple[int, int, int]) -> Image.Image:
        """A labeled action key (e.g. Pause / Cancel / Finish)."""
        img, draw = self._canvas()
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.42), cy=int(self.h * 0.36), color=color)
        self._draw_label(draw, label, y=int(self.h * 0.66), size=13)
        return img

    def climate_status(self, entity: DeviceEntity) -> Image.Image:
        """Thermostat status: hvac mode, the big target set-point, current temp."""
        img, draw = self._canvas()
        color = CLIMATE_ICON if entity.climate_is_on else NEUTRAL
        mode = (entity.state or "").replace("_", " ").upper()
        self._draw_label(draw, mode, y=int(self.h * 0.06), size=12, color=color, max_lines=1)
        target = entity.target_temperature
        target_text = f"{_format_number(target)}°" if target is not None else "—"
        draw.text((self.w / 2, self.h * 0.46), target_text, font=self._value_font(int(self.h * 0.34)), fill=TEXT, anchor="mm")
        current = entity.attributes.get("current_temperature")
        if current is not None:
            self._draw_label(draw, f"now {_format_number(current)}°", y=int(self.h * 0.74), size=11, color=NAV_COLOR, max_lines=1)
        return img

    def climate_power(self, entity: DeviceEntity) -> Image.Image:
        """On/off toggle for the thermostat, labelled with the action it performs."""
        on = entity.climate_is_on
        return self.action_button("power", "Turn Off" if on else "Turn On", UNAVAILABLE if on else SECURE)

    def option_button(self, icon_name: str, label: str, color: tuple[int, int, int] = NAV_COLOR,
                      active: bool = False) -> Image.Image:
        """A labelled icon tile used for menu items, presets and cover controls.

        ``active`` draws an accent border in ``color`` (e.g. the current preset).
        """
        img, draw = self._canvas()
        if active:
            draw.rectangle([1, 1, self.w - 2, self.h - 2], outline=color, width=max(2, int(self.h * 0.03)))
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.40), cy=int(self.h * 0.36), color=color)
        self._draw_label(draw, label, y=int(self.h * 0.66), size=12)
        return img

    _TOGGLE_LABELS = {
        Status.ON: "On", Status.OFF: "Off", Status.OPEN: "Open",
        Status.SECURE: "Closed", Status.PENDING: "…", Status.UNAVAILABLE: "N/A",
    }

    def toggle_button(self, entity: DeviceEntity) -> Image.Image:
        """The menu's Toggle tile: the device icon in its status color (with the
        off-bar / warning badge like a normal tile) and its current state word."""
        img, draw = self._canvas()
        icon_name = icons.resolve_icon_name(entity.domain, entity.device_class, entity.explicit_icon,
                                            state=entity.state, is_open=entity.closure_open())
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.42), cy=int(self.h * 0.36),
                         color=self._icon_color_for(entity))
        self._draw_label(draw, self._TOGGLE_LABELS.get(entity.status, ""), y=int(self.h * 0.66), size=13)
        if entity.status is Status.UNAVAILABLE:
            self._draw_warning_badge(draw)
        elif entity.is_off:
            self._draw_off_bar(draw)
        return img

    def brightness_cell(self, base: tuple[int, int, int], brightness_pct: int) -> Image.Image:
        """A swatch previewing a brightness level at the light's current color."""
        return self._swatch(base, brightness_pct, None)

    def percent_cell(self, pct: int) -> Image.Image:
        """A swatch previewing a percentage level (fan speed / cover position)."""
        return self._swatch(WEATHER_ACCENT, pct, None)

    def nav(self, kind: str) -> Image.Image:
        """kind: 'back' | 'prev' | 'next'."""
        img, draw = self._canvas()
        glyph_name = {"back": "arrow-left", "prev": "chevron-left", "next": "chevron-right"}.get(kind, "arrow-left")
        label = {"back": "Back", "prev": "Prev", "next": "Next"}.get(kind, "")
        self._draw_glyph(draw, icons.glyph(glyph_name), size=int(self.h * 0.40), cy=int(self.h * 0.36), color=NAV_COLOR)
        self._draw_label(draw, label, y=int(self.h * 0.64), size=12, color=NAV_COLOR, max_lines=1)
        return img

    def _swatch(self, base: tuple[int, int, int], brightness_pct: int, sublabel: str | None) -> Image.Image:
        """A preset swatch: base color dimmed by brightness, with a % (and optional) label."""
        factor = max(0.14, brightness_pct / 100)  # keep dim cells faintly visible
        bg = scale(base, factor)
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        # Dark text on light swatches, light text on dark ones.
        luma = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        fg = (20, 20, 20) if luma > 140 else (245, 245, 245)
        y = self.h * 0.40 if sublabel else self.h * 0.5
        draw.text((self.w / 2, y), f"{brightness_pct}%", font=self._value_font(int(self.h * 0.24)), fill=fg, anchor="mm")
        if sublabel:
            draw.text((self.w / 2, self.h * 0.72), sublabel, font=self._label_font(11), fill=fg, anchor="mm")
        return img

    def temp_cell(self, kelvin: int) -> Image.Image:
        """A color-temperature swatch: the kelvin value on its kelvin-colored bg."""
        bg = kelvin_to_rgb(kelvin)
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        # Dark text on light swatches, light text on dark ones.
        luma = 0.299 * bg[0] + 0.587 * bg[1] + 0.114 * bg[2]
        fg = (20, 20, 20) if luma > 140 else (245, 245, 245)
        draw.text((self.w / 2, self.h / 2), f"{kelvin}K", font=self._value_font(int(self.h * 0.22)), fill=fg, anchor="mm")
        return img

    def color_swatch(self, hue: float, saturation: float = 100) -> Image.Image:
        """A plain color swatch (its hue is the label); used by the color picker."""
        return Image.new("RGB", (self.w, self.h), hs_to_rgb(hue, saturation))

    def hold_feedback(self, icon_name: str = "door-open", label: str = "Release to open") -> Image.Image:
        """Shown while a long-press is armed (held past the threshold).

        A filled blue tile so it clearly stands out from the normal key.
        """
        bg = (30, 100, 175)
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.40), cy=int(self.h * 0.34), color=(255, 255, 255))
        self._draw_label(draw, label, y=int(self.h * 0.60), size=12, color=(255, 255, 255))
        return img

    def blank(self) -> Image.Image:
        img, _ = self._canvas()
        return img

    def message(self, text: str, color=UNAVAILABLE) -> Image.Image:
        img, draw = self._canvas()
        self._draw_glyph(draw, icons.glyph("lan-disconnect"), size=int(self.h * 0.34), cy=int(self.h * 0.34), color=color)
        self._draw_label(draw, text, y=int(self.h * 0.60), size=12, color=color, max_lines=2)
        return img


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int) -> list[str]:
    """Greedy word-wrap to <= max_lines, truncating the last line with an ellipsis."""
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if len(lines) < max_lines:
        lines.append(current)

    # Truncate any remaining overflow on the final line.
    if len(lines) == max_lines:
        last = lines[-1]
        if font.getlength(last) > max_width:
            while last and font.getlength(last + "…") > max_width:
                last = last[:-1]
            lines[-1] = (last + "…") if last else "…"
    return lines[:max_lines]
