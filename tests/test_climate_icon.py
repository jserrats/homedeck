import pytest

from homedeck.deck import icons
from homedeck.deck import renderer as rmod
from homedeck.deck.renderer import KeyRenderer
from homedeck.ha.model import DeviceEntity

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _icon_has(img, color):
    w, h = img.size
    for x in range(int(w * 0.2), int(w * 0.8)):
        for y in range(int(h * 0.1), int(h * 0.6)):
            r, g, b = img.getpixel((x, y))
            if abs(r - color[0]) < 30 and abs(g - color[1]) < 30 and abs(b - color[2]) < 30:
                return True
    return False


@requires_assets
def test_fan_on_is_sky_blue():
    img = KeyRenderer((96, 96)).device(DeviceEntity("fan.f", "Fan", "fan", "on"))
    assert _icon_has(img, rmod.CLIMATE_ICON)
    assert not _icon_has(img, rmod.ACCENT)  # not amber


@requires_assets
def test_fan_off_is_grey():
    img = KeyRenderer((96, 96)).device(DeviceEntity("fan.f", "Fan", "fan", "off"))
    assert _icon_has(img, rmod.NEUTRAL)
    assert not _icon_has(img, rmod.CLIMATE_ICON)


@requires_assets
def test_active_climate_is_sky_blue():
    climate = DeviceEntity("climate.t", "Thermostat", "climate", "heat",
                           attributes={"current_temperature": 21})
    img = KeyRenderer((96, 96)).device(climate)
    assert _icon_has(img, rmod.CLIMATE_ICON)


@requires_assets
def test_switch_on_still_amber():
    # a regular switch is unaffected
    img = KeyRenderer((96, 96)).device(DeviceEntity("switch.s", "S", "switch", "on"))
    assert _icon_has(img, rmod.ACCENT)
    assert not _icon_has(img, rmod.CLIMATE_ICON)
