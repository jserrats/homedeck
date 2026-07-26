import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

TOTAL = 32
requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _nav(entity):
    room = Room("hall", "Hall", entities=[entity])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_logbook=lambda e: [])
    return nav, room


def _menu_targets(entity):
    nav, _ = _nav(entity)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=entity)]
    return [a.data["target"] for a in nav._build_key_map().values() if a.kind is ActionKind.MENU_ITEM]


# -- capability flags ---------------------------------------------------------

def test_fan_and_cover_capability_flags():
    fan_preset = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low"]})
    fan_pct = DeviceEntity("fan.b", "B", "fan", "on", attributes={"supported_features": 1, "percentage": 40})
    fan_plain = DeviceEntity("fan.c", "C", "fan", "on", attributes={})
    assert fan_preset.supports_fan_speed and fan_pct.supports_fan_speed
    assert fan_plain.supports_fan_speed is False

    cover_pos = DeviceEntity("cover.a", "A", "cover", "open", attributes={"supported_features": 15, "current_position": 30})
    cover_basic = DeviceEntity("cover.b", "B", "cover", "open", attributes={"supported_features": 3})
    assert cover_pos.supports_cover_position is True
    assert cover_basic.supports_cover_position is False


# -- menus per type -----------------------------------------------------------

def _view(entity, kind):
    nav, _ = _nav(entity)
    nav.stack = [Frame(FrameKind.HOME), Frame(kind, entity=entity)]
    return nav._build_key_map()


def test_plain_fan_has_toggle_and_history():
    # a fan with no speed control doesn't reach the FAN view; its button-menu
    # (Toggle + History) is history-only, so a long press goes straight to History.
    fan = DeviceEntity("fan.c", "C", "fan", "on", attributes={})
    assert _menu_targets(fan) == ["toggle", "history"]


def test_sensor_menu_is_history_only():
    # a read-only sensor can't be toggled, so no Toggle option
    sensor = DeviceEntity("sensor.t", "T", "sensor", "21", attributes={"unit_of_measurement": "°C"})
    assert _menu_targets(sensor) == ["history"]


# -- fan / cover open a combined control view directly ------------------------

def test_long_press_fan_opens_fan_view():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"]})
    nav, _ = _nav(fan)
    nav._open_entity_menu(fan)
    assert nav.stack[-1].kind is FrameKind.FAN


def test_long_press_cover_opens_cover_view():
    cover = DeviceEntity("cover.a", "A", "cover", "open", attributes={"supported_features": 15})
    nav, _ = _nav(cover)
    nav._open_entity_menu(cover)
    assert nav.stack[-1].kind is FrameKind.COVER_ACTIONS


def test_fan_view_shows_toggle_presets_and_history():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"], "preset_mode": "low"})
    view = _view(fan, FrameKind.FAN)
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.MENU_ITEM and view[1].data["target"] == "toggle"
    presets = [a for a in view.values() if a.kind is ActionKind.SERVICE_BUTTON]
    assert [a.data["call"] for a in presets] == [
        ("fan", "set_preset_mode", "fan.a", {"preset_mode": "low"}),
        ("fan", "set_preset_mode", "fan.a", {"preset_mode": "high"}),
    ]
    assert any(a.kind is ActionKind.MENU_ITEM and a.data["target"] == "history" for a in view.values())


def test_fan_view_without_presets_uses_a_speed_picker_button():
    fan = DeviceEntity("fan.b", "B", "fan", "on", attributes={"supported_features": 1, "percentage": 40})
    view = _view(fan, FrameKind.FAN)
    targets = [a.data["target"] for a in view.values() if a.kind is ActionKind.MENU_ITEM]
    assert targets == ["toggle", "fan_speed", "history"]
    # the Speed button opens the full-screen percentage picker
    nav, _ = _nav(fan)
    nav._dispatch_menu_target(fan, "fan_speed")
    assert nav.stack[-1].kind is FrameKind.PICKER
    cells = [a for k, a in sorted(nav._build_key_map().items()) if a.kind is ActionKind.PICKER_CELL]
    assert all(a.data["call"][0:2] == ("fan", "set_percentage") for a in cells)


def test_cover_view_controls_position_history():
    cover = DeviceEntity("cover.a", "A", "cover", "open",
                         attributes={"supported_features": 15, "current_position": 30})
    view = _view(cover, FrameKind.COVER_ACTIONS)
    services = [a.data["call"][1] for k, a in sorted(view.items()) if a.kind is ActionKind.SERVICE_BUTTON]
    assert services == ["open_cover", "stop_cover", "close_cover"]
    menu_targets = [a.data["target"] for a in view.values() if a.kind is ActionKind.MENU_ITEM]
    assert "position" in menu_targets and "history" in menu_targets


def test_basic_cover_view_has_no_position():
    cover = DeviceEntity("cover.b", "B", "cover", "open", attributes={"supported_features": 3})
    view = _view(cover, FrameKind.COVER_ACTIONS)
    menu_targets = [a.data["target"] for a in view.values() if a.kind is ActionKind.MENU_ITEM]
    assert "position" not in menu_targets and "history" in menu_targets


# -- cover controls + position ------------------------------------------------

def test_cover_actions_view_open_stop_close():
    cover = DeviceEntity("cover.a", "A", "cover", "open", attributes={"supported_features": 15})
    nav, _ = _nav(cover)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.COVER_ACTIONS, entity=cover)]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    services = [a.data["call"][1] for k, a in sorted(view.items()) if a.kind is ActionKind.SERVICE_BUTTON]
    assert services == ["open_cover", "stop_cover", "close_cover"]


def test_cover_position_picker_sets_position():
    cover = DeviceEntity("cover.a", "A", "cover", "open",
                         attributes={"supported_features": 15, "current_position": 30})
    nav, _ = _nav(cover)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.PICKER, entity=cover, data={"type": "cover_position"})]
    cells = [a for k, a in sorted(nav._build_key_map().items()) if a.kind is ActionKind.PICKER_CELL]
    assert all(a.data["call"][0:2] == ("cover", "set_cover_position") for a in cells)
    positions = [a.data["call"][3]["position"] for a in cells]
    assert positions[0] == 0 and positions[-1] == 100


@requires_assets
def test_cover_action_button_fires_and_stays():
    calls = []
    cover = DeviceEntity("cover.a", "A", "cover", "open", attributes={"supported_features": 15})
    nav, _ = _nav(cover)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.COVER_ACTIONS, entity=cover)]
    nav.key_map = nav._build_key_map()
    open_key = next(k for k, a in nav.key_map.items()
                    if a.kind is ActionKind.SERVICE_BUTTON and a.data["call"][1] == "open_cover")
    nav.handle_press(open_key, True)
    assert calls == [("cover", "open_cover", "cover.a", {})]
    assert nav.stack[-1].kind is FrameKind.COVER_ACTIONS  # controls stay open


# -- read-only sensor: long press -> history, short press -> nothing ----------

@requires_assets
def test_sensor_long_press_history_short_press_noop(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    sensor = DeviceEntity("sensor.t", "Temp", "sensor", "21", attributes={"unit_of_measurement": "°C"})
    nav, room = _nav(sensor)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)

    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)          # short press: read-only, nothing happens
    assert calls == [] and nav.stack[-1].kind is FrameKind.ROOM

    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)          # long press: single option -> History
    assert nav.stack[-1].kind is FrameKind.HISTORY


@requires_assets
def test_fan_and_cover_views_render():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"], "preset_mode": "low"})
    cover = DeviceEntity("cover.a", "A", "cover", "open", attributes={"supported_features": 15, "current_position": 30})
    nav, _ = _nav(fan)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.FAN, entity=fan)]
    nav.render()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.COVER_ACTIONS, entity=cover)]
    nav.render()  # both render without error
