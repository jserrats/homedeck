import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import (
    CLIMATE_AREA,
    ActionKind,
    Frame,
    FrameKind,
    Navigation,
)

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

COLS = 8


def _ent(eid, domain, state, device_class=None, name=None):
    return DeviceEntity(
        eid, name or eid.split(".")[-1], domain, state,
        attributes={"device_class": device_class} if device_class else {},
        device_class=device_class,
    )


def _nav():
    rooms = [
        Room("living", "Living Room", icon="mdi:sofa", entities=[
            _ent("light.lamp", "light", "on"),                              # not climate
            _ent("sensor.living_temp", "sensor", "21.4", "temperature"),
            _ent("fan.living", "fan", "on"),
            _ent("climate.living", "climate", "heat"),
        ]),
        Room("bedroom", "Bedroom", icon="mdi:bed", entities=[
            _ent("sensor.bed_temp", "sensor", "19", "temperature"),
            _ent("sensor.bed_humidity", "sensor", "55", "humidity"),        # not climate
        ]),
    ]
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), rooms, on_service=lambda c: None)
    return nav


def test_climate_folder_pinned_in_band():
    nav = _nav()
    home = nav._build_key_map()
    climate = [k for k, a in home.items() if a.kind is ActionKind.OPEN_CLIMATE]
    assert len(climate) == 1
    assert home[climate[0]].room.area_id == CLIMATE_AREA
    assert all(k >= 24 for k in climate)  # in the reserved bottom band


def test_climate_groups_contents():
    nav = _nav()
    groups = nav._collect_climate_groups()
    kinds_ids = [[(a.kind.name, a.entity.entity_id) for a in g] for g in groups]
    # temperature sensors (labelled by room), then fans, then thermostats;
    # lights and humidity are excluded.
    assert kinds_ids == [
        [("CLIMATE_TEMP", "sensor.bed_temp"), ("CLIMATE_TEMP", "sensor.living_temp")],
        [("ENTITY", "fan.living")],
        [("ENTITY", "climate.living")],
    ]


def test_temperature_tiles_carry_their_room():
    nav = _nav()
    temps = nav._collect_climate_groups()[0]
    by_id = {a.entity.entity_id: a for a in temps}
    assert by_id["sensor.living_temp"].room.name == "Living Room"
    assert by_id["sensor.bed_temp"].room.name == "Bedroom"


def test_opening_climate_pushes_frame():
    nav = _nav()
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_CLIMATE)
    nav.handle_press(key, pressed=True)
    assert nav.stack[-1].kind is FrameKind.CLIMATE


def test_climate_view_one_type_per_column():
    nav = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE)]
    key_map = nav._build_key_map()
    assert key_map[0].kind is ActionKind.BACK

    def cols_for(kind, predicate=lambda a: True):
        return {k % COLS for k, a in key_map.items() if a.kind is kind and predicate(a)}

    temp_cols = cols_for(ActionKind.CLIMATE_TEMP)
    fan_cols = cols_for(ActionKind.ENTITY, lambda a: a.entity.domain == "fan")
    clim_cols = cols_for(ActionKind.ENTITY, lambda a: a.entity.domain == "climate")

    assert temp_cols and fan_cols and clim_cols
    assert temp_cols.isdisjoint(fan_cols)
    assert temp_cols.isdisjoint(clim_cols)
    assert fan_cols.isdisjoint(clim_cols)
    assert min(temp_cols | fan_cols | clim_cols) >= 1  # right of the Back column


def test_temperature_tile_is_not_interactive():
    calls = []
    nav = _nav()
    nav.on_service = lambda c: calls.append(c)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE)]
    nav.key_map = nav._build_key_map()
    temp_key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.CLIMATE_TEMP)
    nav.handle_press(temp_key, True)
    nav.handle_press(temp_key, False)
    assert calls == []  # a temperature readout does nothing on press
    assert nav.stack[-1].kind is FrameKind.CLIMATE


@requires_assets
def test_temperature_tile_renders_room_name_not_entity():
    nav = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE)]
    nav.render()
    # the living-room temperature tile should differ from a plain device tile
    # (room icon + room name), while still showing the reading.
    living = next(e for e in nav.rooms[0].entities if e.entity_id == "sensor.living_temp")
    room_tile = nav.renderer.climate_room_reading(living, nav.rooms[0]).tobytes()
    device_tile = nav.renderer.device(living).tobytes()
    assert room_tile != device_tile


# -- thermostat control -------------------------------------------------------

def _thermostat(state="heat", **attrs):
    base = {"current_temperature": 19.0, "temperature": 21.0, "target_temp_step": 0.5,
            "min_temp": 7, "max_temp": 30, "preset_modes": ["eco", "comfort", "boost"],
            "preset_mode": "comfort"}
    base.update(attrs)
    return DeviceEntity("climate.living", "Thermostat", "climate", state, attributes=base,
                        device_class=None)


def _thermo_nav(state="heat", **attrs):
    room = Room("living", "Living", entities=[_thermostat(state, **attrs)])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.key_map = nav._build_key_map()
    return nav


def test_climate_is_controllable_and_has_long_press():
    t = _thermostat()
    assert t.is_controllable
    assert t.has_long_press
    assert t.is_climate


def test_climate_single_press_toggles_power():
    on = _thermostat("heat")
    assert on.service_call() == ("climate", "turn_off", "climate.living", {})
    off = _thermostat("off")
    assert off.service_call() == ("climate", "turn_on", "climate.living", {})


def test_short_press_toggles_long_press_opens_detail(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    nav = _thermo_nav("heat")
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=nav.rooms[0])]
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.domain == "climate")

    # short press toggles off (currently heating)
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)
    assert calls == [("climate", "turn_off", "climate.living", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    # long press opens the thermostat detail view
    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.CLIMATE_DETAIL


def test_detail_layout_has_status_adjust_power_and_presets():
    nav = _thermo_nav("heat")
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE_DETAIL, entity=_thermostat())]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.CLIMATE_STATUS
    assert view[2].kind is ActionKind.CLIMATE_ADJUST and view[2].delta == -1
    assert view[3].kind is ActionKind.CLIMATE_ADJUST and view[3].delta == 1
    assert view[4].kind is ActionKind.CLIMATE_POWER
    presets = [a.data["preset"] for a in view.values() if a.kind is ActionKind.CLIMATE_PRESET]
    assert presets == ["eco", "comfort", "boost"]


def test_pressing_adjust_sets_new_target_temperature():
    calls = []
    nav = _thermo_nav("heat")
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE_DETAIL, entity=_thermostat())]
    nav.key_map = nav._build_key_map()
    nav.handle_press(3, True)  # +1°  (target 21.0 -> 22)
    assert calls == [("climate", "set_temperature", "climate.living", {"temperature": 22})]
    calls.clear()
    nav.handle_press(2, True)  # -1°  (target 21.0 -> 20)
    assert calls == [("climate", "set_temperature", "climate.living", {"temperature": 20})]
    assert nav.stack[-1].kind is FrameKind.CLIMATE_DETAIL  # stays in the detail view


def test_adjust_clamps_to_thermostat_range():
    t = _thermostat(temperature=30.0, max_temp=30)
    # +step would exceed max_temp -> clamped, integer collapsed
    assert t.climate_set_temperature_call(30.5) == ("climate", "set_temperature", "climate.living", {"temperature": 30})


def test_pressing_preset_sets_preset_mode():
    calls = []
    nav = _thermo_nav("heat")
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE_DETAIL, entity=_thermostat())]
    nav.key_map = nav._build_key_map()
    eco_key = next(k for k, a in nav.key_map.items()
                   if a.kind is ActionKind.CLIMATE_PRESET and a.data["preset"] == "eco")
    nav.handle_press(eco_key, True)
    assert calls == [("climate", "set_preset_mode", "climate.living", {"preset_mode": "eco"})]


def test_pressing_power_toggles():
    calls = []
    nav = _thermo_nav("heat")
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE_DETAIL, entity=_thermostat("heat"))]
    nav.key_map = nav._build_key_map()
    nav.handle_press(4, True)  # power
    assert calls == [("climate", "turn_off", "climate.living", {})]


@requires_assets
def test_detail_view_renders():
    nav = _thermo_nav("heat")
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.CLIMATE_DETAIL, entity=_thermostat())]
    nav.render()  # must not raise; all tiles render
