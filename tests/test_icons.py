import pytest

from homedeck.deck import icons

# These tests need the fetched MDI metadata; skip cleanly if it's absent.
pytestmark = pytest.mark.skipif(
    not icons.META_PATH.exists(),
    reason="MDI assets not fetched; run scripts/fetch_assets.py",
)


def test_explicit_icon_wins():
    assert icons.resolve_icon_name("light", None, "mdi:ceiling-light") == "ceiling-light"


def test_explicit_icon_strips_prefix_and_validates():
    # Unknown explicit icon falls through to the domain default.
    assert icons.resolve_icon_name("light", None, "mdi:not-a-real-icon") == "lightbulb"


def test_device_class_default():
    assert icons.resolve_icon_name("sensor", "temperature", None) == "thermometer"
    assert icons.resolve_icon_name("sensor", "humidity", None) == "water-percent"


def test_domain_default():
    assert icons.resolve_icon_name("fan", None, None) == "fan"
    assert icons.resolve_icon_name("cover", None, None) == "window-shutter"


def test_unknown_domain_generic_fallback():
    assert icons.resolve_icon_name("nonexistent_domain", None, None) == icons.GENERIC_FALLBACK


def test_glyph_returns_single_char():
    g = icons.glyph("lightbulb")
    assert isinstance(g, str) and len(g) == 1


# -- binary_sensor device-class icons (mirrors HA's own state-aware mapping) ---

def test_binary_sensor_device_class_icons_follow_state():
    assert icons.resolve_icon_name("binary_sensor", "battery", None, state="on") == "battery-outline"
    assert icons.resolve_icon_name("binary_sensor", "battery", None, state="off") == "battery"
    assert icons.resolve_icon_name("binary_sensor", "problem", None, state="on") == "alert-circle"
    assert icons.resolve_icon_name("binary_sensor", "problem", None, state="off") == "check-circle"
    assert icons.resolve_icon_name("binary_sensor", "motion", None, state="on") == "motion-sensor"
    assert icons.resolve_icon_name("binary_sensor", "motion", None, state="off") == "motion-sensor-off"
    assert icons.resolve_icon_name("binary_sensor", "connectivity", None, state="on") == "check-network-outline"
    assert icons.resolve_icon_name("binary_sensor", "running", None, state="off") == "stop"


def test_binary_sensor_without_device_class_is_the_ha_dot():
    assert icons.resolve_icon_name("binary_sensor", None, None, state="on") == "checkbox-marked-circle"
    assert icons.resolve_icon_name("binary_sensor", None, None, state="off") == "radiobox-blank"


def test_binary_sensor_explicit_icon_still_wins():
    assert icons.resolve_icon_name("binary_sensor", "smoke", "mdi:fridge", state="on") == "fridge"


def test_binary_sensor_unmapped_device_class_keeps_its_default():
    # 'moving' has no state-aware pair; it keeps the (domain, device_class) default
    assert icons.resolve_icon_name("binary_sensor", "moving", None, state="on") == "walk"
    # a closure with an unknown state falls back to the static default, not the dot
    assert icons.resolve_icon_name("binary_sensor", "door", None, state="unavailable") == "door"
