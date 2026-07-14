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

def test_fan_menu_has_toggle_speed_then_history():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"]})
    assert _menu_targets(fan) == ["toggle", "fan_speed", "history"]


def test_plain_fan_has_toggle_and_history():
    fan = DeviceEntity("fan.c", "C", "fan", "on", attributes={})
    assert _menu_targets(fan) == ["toggle", "history"]


def test_cover_menu_toggle_controls_position_history():
    cover = DeviceEntity("cover.a", "A", "cover", "open",
                         attributes={"supported_features": 15, "current_position": 30})
    assert _menu_targets(cover) == ["toggle", "cover", "position", "history"]


def test_basic_cover_menu_has_no_position():
    cover = DeviceEntity("cover.b", "B", "cover", "open", attributes={"supported_features": 3})
    assert _menu_targets(cover) == ["toggle", "cover", "history"]


def test_sensor_menu_is_history_only():
    # a read-only sensor can't be toggled, so no Toggle option
    sensor = DeviceEntity("sensor.t", "T", "sensor", "21", attributes={"unit_of_measurement": "°C"})
    assert _menu_targets(sensor) == ["history"]


# -- fan speed: presets vs percentage -----------------------------------------

def test_fan_speed_opens_presets_when_available():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"], "preset_mode": "low"})
    nav, _ = _nav(fan)
    nav._dispatch_menu_target(fan, "fan_speed")
    assert nav.stack[-1].kind is FrameKind.PRESETS
    view = nav._build_key_map()
    presets = [a for a in view.values() if a.kind is ActionKind.SERVICE_BUTTON]
    assert [a.data["call"] for a in presets] == [
        ("fan", "set_preset_mode", "fan.a", {"preset_mode": "low"}),
        ("fan", "set_preset_mode", "fan.a", {"preset_mode": "high"}),
    ]
    assert view[1].kind is ActionKind.SERVICE_BUTTON  # no status tile for a fan (not climate)


def test_fan_speed_opens_percentage_picker_without_presets():
    fan = DeviceEntity("fan.b", "B", "fan", "on", attributes={"supported_features": 1, "percentage": 40})
    nav, _ = _nav(fan)
    nav._dispatch_menu_target(fan, "fan_speed")
    assert nav.stack[-1].kind is FrameKind.PICKER
    cells = [a for k, a in sorted(nav._build_key_map().items()) if a.kind is ActionKind.PICKER_CELL]
    assert all(a.data["call"][0:2] == ("fan", "set_percentage") for a in cells)
    pcts = [a.data["call"][3]["percentage"] for a in cells]
    assert pcts == sorted(pcts) and pcts[0] == 10 and pcts[-1] == 100


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
    nav.handle_press(1, True)  # Open
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
def test_menu_and_presets_render():
    fan = DeviceEntity("fan.a", "A", "fan", "on", attributes={"preset_modes": ["low", "high"], "preset_mode": "low"})
    nav, _ = _nav(fan)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=fan)]
    nav.render()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.PRESETS, entity=fan)]
    nav.render()  # both render without error
