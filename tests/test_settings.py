import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import (
    SETTINGS_AREA,
    ActionKind,
    Frame,
    FrameKind,
    Navigation,
)

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _nav(on_reload=None, weather=None):
    room = Room("living", "Living", entities=[DeviceEntity("light.l", "L", "light", "on")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None,
                     on_reload=on_reload, weather=weather)
    return nav, room


def _special_keys(key_map):
    return [k for k, a in key_map.items()
            if a.kind in (ActionKind.OPEN_SECURITY, ActionKind.OPEN_WEATHER, ActionKind.OPEN_SETTINGS)
            or (a.kind is ActionKind.OPEN_ROOM and a.room.is_dynamic)]


def test_settings_folder_is_always_last_special():
    nav, _ = _nav()
    home = nav._build_key_map()
    settings_key = next(k for k, a in home.items() if a.kind is ActionKind.OPEN_SETTINGS)
    assert home[settings_key].room.area_id == SETTINGS_AREA
    assert settings_key == max(_special_keys(home))  # displayed last among the specials


def test_settings_stays_last_even_with_weather():
    from homedeck.ha.weather import Weather
    nav, _ = _nav(weather=Weather("weather.home", "sunny", 20.0))
    home = nav._build_key_map()
    settings_key = next(k for k, a in home.items() if a.kind is ActionKind.OPEN_SETTINGS)
    weather_key = next(k for k, a in home.items() if a.kind is ActionKind.OPEN_WEATHER)
    assert settings_key > weather_key


def test_settings_view_has_reload_button():
    nav, _ = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.SETTINGS_ITEM
    assert view[1].data["action"] == "reload"


def test_pressing_reload_invokes_callback():
    calls = []
    nav, _ = _nav(on_reload=lambda: calls.append(True))
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    nav.key_map = nav._build_key_map()
    nav.handle_press(1, True)  # the Reload item
    assert calls == [True]


def test_reload_failure_does_not_crash():
    def boom():
        raise RuntimeError("HA down")

    nav, _ = _nav(on_reload=boom)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    nav.key_map = nav._build_key_map()
    nav.handle_press(1, True)  # must be swallowed


@requires_assets
def test_set_model_swaps_and_returns_home():
    nav, _ = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    new_room = Room("kitchen", "Kitchen", entities=[DeviceEntity("light.k", "K", "light", "off")])
    nav.set_model([new_room], [], [], None)
    assert nav.rooms == [new_room]
    assert nav.stack[-1].kind is FrameKind.HOME


# -- rotate setting -----------------------------------------------------------

def test_settings_has_rotate_item():
    nav, _ = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    view = nav._build_key_map()
    rotate = next(a for a in view.values()
                  if a.kind is ActionKind.SETTINGS_ITEM and a.data.get("action") == "rotate")
    assert rotate is not None


def test_pressing_rotate_invokes_callback():
    calls = []
    room = Room("living", "Living", entities=[DeviceEntity("light.l", "L", "light", "on")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_rotate=lambda: calls.append(True))
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    nav.key_map = nav._build_key_map()
    rotate_key = next(k for k, a in nav.key_map.items()
                      if a.kind is ActionKind.SETTINGS_ITEM and a.data.get("action") == "rotate")
    nav.handle_press(rotate_key, True)
    assert calls == [True]


def test_portrait_home_pins_specials_to_bottom_row():
    # A 4-wide (portrait) display -> 8 rows; specials sit on the last row (28..31).
    display = ExportDisplay(cols=4)
    room = Room("living", "Living", entities=[DeviceEntity("light.l", "L", "light", "on")])
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    home = nav._build_key_map()
    assert all(k >= 28 for k in _special_keys(home))
