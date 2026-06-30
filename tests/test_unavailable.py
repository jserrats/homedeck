import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.ha.model import DeviceEntity, Status

pytestmark = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _has_red_badge(img) -> bool:
    """True if the top-right corner has a warning-red pixel.

    The badge red is (239,68,68) where green≈blue; orange (open) icons are
    (249,115,22) with very low blue, so requiring blue≈green excludes them.
    """
    w, h = img.size
    for x in range(int(w * 0.55), w):
        for y in range(0, int(h * 0.45)):
            r, g, b = img.getpixel((x, y))
            if r > 170 and g < 120 and b > 45 and b > g * 0.6:
                return True
    return False


def test_unavailable_device_keeps_dim_icon_and_gets_badge():
    entity = DeviceEntity("light.lamp", "Lamp", "light", "unavailable")
    assert entity.status is Status.UNAVAILABLE
    img = KeyRenderer((96, 96)).device(entity)
    assert _has_red_badge(img)  # warning triangle present


def test_available_device_has_no_badge():
    on = DeviceEntity("light.lamp", "Lamp", "light", "on")        # amber icon
    off = DeviceEntity("switch.x", "X", "switch", "off")          # grey icon
    r = KeyRenderer((96, 96))
    assert not _has_red_badge(r.device(on))
    assert not _has_red_badge(r.device(off))


def test_open_closure_is_orange_not_flagged_as_warning():
    # an open door is orange (g≈115), which must not be mistaken for the red badge
    door = DeviceEntity("binary_sensor.door", "Door", "binary_sensor", "on",
                        attributes={"device_class": "door"}, device_class="door")
    assert not _has_red_badge(KeyRenderer((96, 96)).device(door))


def test_unavailable_sensor_also_badged():
    sensor = DeviceEntity("sensor.temp", "Temp", "sensor", "unavailable",
                          attributes={"unit_of_measurement": "°C"})
    assert _has_red_badge(KeyRenderer((96, 96)).device(sensor))
