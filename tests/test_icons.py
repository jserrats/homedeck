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
