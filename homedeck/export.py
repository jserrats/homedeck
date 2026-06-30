"""Offscreen render target: compose the 8x4 key grid into PNG files.

Lets you verify layout, icons, and colors without a Stream Deck attached:

    homedeck --export ./export
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class ExportDisplay:
    """Implements the navigation ``Display`` surface, collecting key images."""

    def __init__(self, key_count: int = 32, cols: int = 8, key_size: tuple[int, int] = (96, 96)) -> None:
        self.key_count = key_count
        self.cols = cols
        self.key_size = key_size
        self.images: dict[int, Image.Image] = {}

    def set_image(self, key: int, image: Image.Image) -> None:
        self.images[key] = image

    def reset(self) -> None:
        self.images = {}

    def grid(self, gap: int = 6, bg=(40, 40, 44)) -> Image.Image:
        rows = (self.key_count + self.cols - 1) // self.cols
        kw, kh = self.key_size
        width = self.cols * kw + (self.cols + 1) * gap
        height = rows * kh + (rows + 1) * gap
        canvas = Image.new("RGB", (width, height), bg)
        for key in range(self.key_count):
            r, c = divmod(key, self.cols)
            x = gap + c * (kw + gap)
            y = gap + r * (kh + gap)
            img = self.images.get(key) or Image.new("RGB", self.key_size, (16, 16, 18))
            canvas.paste(img, (x, y))
        return canvas


def _slug(name: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-") or "room"


def export_views(rooms, navigation, display: ExportDisplay, out_dir: str) -> list[Path]:
    """Render the home screen and each room to PNG grids; return written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    display.reset()
    navigation.home()
    home_path = out / "00-home.png"
    display.grid().save(home_path)
    written.append(home_path)

    for i, room in enumerate(rooms, start=1):
        display.reset()
        navigation.open_room(room)
        path = out / f"{i:02d}-{_slug(room.name)}.png"
        display.grid().save(path)
        written.append(path)

    return written
