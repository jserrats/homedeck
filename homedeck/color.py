"""Small color helpers shared by the renderer and the light model."""

from __future__ import annotations

import colorsys
import math


def clamp8(v: float) -> int:
    return max(0, min(255, int(round(v))))


def scale(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return (clamp8(rgb[0] * factor), clamp8(rgb[1] * factor), clamp8(rgb[2] * factor))


def hs_to_rgb(hue: float, saturation: float) -> tuple[int, int, int]:
    """HA hs_color (hue 0-360, saturation 0-100) -> full-value RGB."""
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360.0, max(0.0, min(100.0, saturation)) / 100.0, 1.0)
    return (clamp8(r * 255), clamp8(g * 255), clamp8(b * 255))


def kelvin_to_rgb(kelvin: int) -> tuple[int, int, int]:
    """Approximate the RGB white point of a color temperature (Tanner Helland)."""
    t = max(1000, min(40000, kelvin)) / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
    else:
        r = 329.698727446 * ((t - 60) ** -0.1332047592)
        g = 288.1221695283 * ((t - 60) ** -0.0755148492)
    if t >= 66:
        b = 255.0
    elif t <= 19:
        b = 0.0
    else:
        b = 138.5177312231 * math.log(t - 10) - 305.0447927307
    return (clamp8(r), clamp8(g), clamp8(b))
