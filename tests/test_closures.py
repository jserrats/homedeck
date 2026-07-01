import pytest

from homedeck.deck import icons
from homedeck.ha.model import DeviceEntity, Status


def _bs(device_class, state):
    return DeviceEntity(
        f"binary_sensor.{device_class}", device_class, "binary_sensor", state,
        attributes={"device_class": device_class}, device_class=device_class,
    )


def _cover(device_class, state):
    return DeviceEntity(
        f"cover.{device_class}", device_class, "cover", state,
        attributes={"device_class": device_class}, device_class=device_class,
    )


# -- color (status) -----------------------------------------------------------

@pytest.mark.parametrize("device_class", ["door", "window", "garage_door", "opening"])
def test_binary_sensor_closure_open_is_orange_closed_is_green(device_class):
    assert _bs(device_class, "on").status is Status.OPEN     # on = open -> orange
    assert _bs(device_class, "off").status is Status.SECURE  # off = closed -> green


def test_cover_door_closed_green_open_orange():
    assert _cover("door", "closed").status is Status.SECURE
    assert _cover("door", "open").status is Status.OPEN
    assert _cover("garage", "closing").status is Status.PENDING


def test_closure_unavailable_is_unavailable():
    assert _bs("door", "unavailable").status is Status.UNAVAILABLE


def test_non_closure_binary_sensor_keeps_default_palette():
    # a motion sensor is not a closure: on -> ON (accent), off -> OFF
    assert _bs("motion", "on").status is Status.ON
    assert _bs("motion", "off").status is Status.OFF


def test_all_covers_use_open_closed_colors():
    # every cover (even non-door blinds) is green when closed / orange when open
    assert _cover("blind", "open").status is Status.OPEN
    assert _cover("blind", "closed").status is Status.SECURE
    # ...but a blind is still not a security "closure" (excluded from that grouping)
    assert _cover("blind", "open").is_closure is False


def test_closure_open_helper():
    assert _bs("door", "on").closure_open() is True
    assert _bs("door", "off").closure_open() is False
    assert _cover("door", "closed").closure_open() is False
    assert _cover("door", "opening").closure_open() is None
    assert _bs("motion", "on").closure_open() is None  # not a closure


# -- icons --------------------------------------------------------------------

@pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")
def test_closure_icons_reflect_open_closed():
    assert icons.resolve_icon_name("binary_sensor", "door", None, is_open=True) == "door-open"
    assert icons.resolve_icon_name("binary_sensor", "door", None, is_open=False) == "door"
    assert icons.resolve_icon_name("cover", "garage", None, is_open=True) == "garage-open"
    assert icons.resolve_icon_name("binary_sensor", "window", None, is_open=False) == "window-closed-variant"
    # explicit icon still wins
    assert icons.resolve_icon_name("binary_sensor", "door", "mdi:fridge", is_open=True) == "fridge"
