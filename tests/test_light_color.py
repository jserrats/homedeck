from homedeck.color import hs_to_rgb, kelvin_to_rgb, scale
from homedeck.ha.model import WARM_WHITE, DeviceEntity


# -- color util ---------------------------------------------------------------

def test_hs_to_rgb_primaries():
    assert hs_to_rgb(0, 100) == (255, 0, 0)      # red
    assert hs_to_rgb(120, 100) == (0, 255, 0)    # green
    assert hs_to_rgb(240, 100) == (0, 0, 255)    # blue


def test_kelvin_warm_vs_cool():
    warm = kelvin_to_rgb(2200)
    cool = kelvin_to_rgb(6500)
    assert warm[0] >= warm[2]          # warm: red >= blue
    assert cool[2] > warm[2]           # cool has more blue than warm


def test_scale():
    assert scale((200, 100, 50), 0.5) == (100, 50, 25)


# -- entity.icon_color --------------------------------------------------------

def _light(state="on", **attrs):
    attrs.setdefault("supported_color_modes", ["color_temp", "hs"])
    return DeviceEntity("light.x", "X", "light", state, attributes=attrs, device_class=None)


def test_plain_onoff_light_has_no_dynamic_color():
    light = DeviceEntity("light.plain", "P", "light", "on",
                         attributes={"supported_color_modes": ["onoff"]})
    assert light.supports_dynamic_color is False
    assert light.icon_color() is None  # falls back to the status palette


def test_off_or_unavailable_light_has_no_dynamic_color():
    assert _light("off").icon_color() is None
    assert _light("unavailable").icon_color() is None


def test_rgb_color_used_when_in_color_mode():
    light = _light(color_mode="hs", rgb_color=[10, 200, 40], brightness=255)
    assert light.icon_color() == (10, 200, 40)


def test_hs_color_converted_when_no_rgb():
    light = _light(color_mode="hs", hs_color=[240, 100], brightness=255)
    assert light.icon_color() == (0, 0, 255)  # blue


def test_color_temp_kelvin_used():
    light = _light(color_mode="color_temp", color_temp_kelvin=2200, brightness=255)
    r, g, b = light.icon_color()
    assert r >= b  # warm white


def test_brightness_dims_the_tint():
    bright = _light(color_mode="hs", rgb_color=[200, 200, 200], brightness=255).icon_color()
    dim = _light(color_mode="hs", rgb_color=[200, 200, 200], brightness=64).icon_color()
    assert dim[0] < bright[0]           # dimmer -> darker
    assert all(c > 0 for c in dim)      # floor keeps it visible


def test_brightness_only_light_uses_warm_white():
    light = DeviceEntity("light.dim", "D", "light", "on",
                         attributes={"supported_color_modes": ["brightness"], "brightness": 255})
    assert light.icon_color() == WARM_WHITE
