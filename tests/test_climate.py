import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
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
