"""Render Stream Deck key images with Pillow.

Produces plain RGB ``PIL.Image`` objects at the key's pixel size; converting to
the deck's native format is the controller's job, which keeps the renderer
usable without any hardware attached (e.g. for ``--export``).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import icons
from ..ha.model import DeviceEntity, Floor, Room, Status

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
LABEL_FONT = ASSETS_DIR / "DejaVuSans.ttf"
VALUE_FONT = ASSETS_DIR / "DejaVuSans-Bold.ttf"

# Palette
BG = (16, 16, 18)
TEXT = (236, 236, 238)
ACCENT = (255, 176, 0)       # on (lights/switches/...)
NEUTRAL = (120, 120, 126)    # off / informational
UNAVAILABLE = (208, 64, 52)  # unavailable / error
SECURE = (34, 197, 94)       # locked, or a closed door/window (green)
OPEN = (249, 115, 22)        # an open door/window/closure (orange)
PENDING = (250, 204, 21)     # transitional, e.g. locking/unlocking (yellow)
ROOM_ACCENT = (96, 165, 250)   # room folders
FLOOR_ACCENT = (52, 211, 153)  # floor folders
NAV_COLOR = (210, 210, 214)

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

    def _draw_glyph(self, draw, glyph: str, size: int, cy: int, color) -> None:
        font = self._icon_font(size)
        draw.text((self.w / 2, cy), glyph, font=font, fill=color, anchor="mm")

    def _draw_label(self, draw, text: str, *, y: int, size: int, color=TEXT, max_lines: int = 2) -> None:
        font = self._label_font(size)
        lines = _wrap(text, font, self.w - 6, max_lines)
        line_h = size + 2
        start_y = y
        for i, line in enumerate(lines):
            draw.text((self.w / 2, start_y + i * line_h), line, font=font, fill=color, anchor="ma")

    # -- key types ----------------------------------------------------------

    def device(self, entity: DeviceEntity) -> Image.Image:
        img, draw = self._canvas()
        color = STATUS_COLORS[entity.status]
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
        else:
            # Controllable: large colored icon, name at the bottom.
            self._draw_glyph(draw, glyph, size=int(self.h * 0.46), cy=int(self.h * 0.38), color=color)
            self._draw_label(draw, entity.name, y=int(self.h * 0.66), size=13)
        return img

    def room(self, room: Room, dynamic: bool = False) -> Image.Image:
        img, draw = self._canvas()
        icon_name = icons.resolve_icon_name("", None, room.icon) or "door"
        if icon_name == icons.GENERIC_FALLBACK:
            icon_name = "door"  # nicer default for a room/folder than a question mark
        # Dynamic folders (e.g. "Lights On") use the amber accent to set them
        # apart from the blue area folders.
        color = ACCENT if dynamic else ROOM_ACCENT
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.42), cy=int(self.h * 0.36), color=color)
        self._draw_label(draw, room.name, y=int(self.h * 0.64), size=13)
        return img

    def floor_header(self, floor: Floor) -> Image.Image:
        """A non-interactive section label marking the start of a floor's rooms."""
        bg = (18, 60, 48)  # dark teal so the floor name reads in light text
        img = Image.new("RGB", (self.w, self.h), bg)
        draw = ImageDraw.Draw(img)
        icon_name = icons.resolve_icon_name("", None, floor.icon) or "floor-plan"
        if icon_name == icons.GENERIC_FALLBACK:
            icon_name = "floor-plan"
        self._draw_glyph(draw, icons.glyph(icon_name), size=int(self.h * 0.34), cy=int(self.h * 0.32), color=FLOOR_ACCENT)
        self._draw_label(draw, floor.name, y=int(self.h * 0.58), size=13, color=(220, 245, 238))
        return img

    def nav(self, kind: str) -> Image.Image:
        """kind: 'back' | 'prev' | 'next'."""
        img, draw = self._canvas()
        glyph_name = {"back": "arrow-left", "prev": "chevron-left", "next": "chevron-right"}.get(kind, "arrow-left")
        label = {"back": "Back", "prev": "Prev", "next": "Next"}.get(kind, "")
        self._draw_glyph(draw, icons.glyph(glyph_name), size=int(self.h * 0.40), cy=int(self.h * 0.36), color=NAV_COLOR)
        self._draw_label(draw, label, y=int(self.h * 0.64), size=12, color=NAV_COLOR, max_lines=1)
        return img

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
