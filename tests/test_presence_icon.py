import pytest

from homedeck.deck import icons
from homedeck.deck import renderer as rmod
from homedeck.deck.renderer import KeyRenderer
from homedeck.ha.model import DeviceEntity

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _motion(state):
    return DeviceEntity("binary_sensor.m", "Motion", "binary_sensor", state,
                        attributes={"device_class": "motion"}, device_class="motion")


def _icon_has(img, color):
    """Any pixel near the icon (center band) close to `color`?"""
    w, h = img.size
    for x in range(int(w * 0.2), int(w * 0.8)):
        for y in range(int(h * 0.1), int(h * 0.6)):
            r, g, b = img.getpixel((x, y))
            if abs(r - color[0]) < 30 and abs(g - color[1]) < 30 and abs(b - color[2]) < 30:
                return True
    return False


@requires_assets
def test_presence_icon_purple_when_detecting():
    img = KeyRenderer((96, 96)).device(_motion("on"))
    assert _icon_has(img, rmod.DOT_PRESENCE)      # purple
    assert not _icon_has(img, rmod.ACCENT)        # not amber


@requires_assets
def test_presence_icon_grey_when_clear():
    img = KeyRenderer((96, 96)).device(_motion("off"))
    assert _icon_has(img, rmod.NEUTRAL)           # off -> grey
    assert not _icon_has(img, rmod.DOT_PRESENCE)


@requires_assets
def test_occupancy_also_purple():
    occ = DeviceEntity("binary_sensor.o", "Occ", "binary_sensor", "on",
                       attributes={"device_class": "occupancy"}, device_class="occupancy")
    assert _icon_has(KeyRenderer((96, 96)).device(occ), rmod.DOT_PRESENCE)


@requires_assets
def test_non_presence_binary_sensor_stays_amber():
    # a generic binary_sensor on is still amber (ON), not purple
    bs = DeviceEntity("binary_sensor.x", "X", "binary_sensor", "on")
    img = KeyRenderer((96, 96)).device(bs)
    assert _icon_has(img, rmod.ACCENT)
    assert not _icon_has(img, rmod.DOT_PRESENCE)
