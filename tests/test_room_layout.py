from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import (
    ActionKind,
    Frame,
    FrameKind,
    Navigation,
    layout_room,
)

COLS = 8
TOTAL = 32  # Stream Deck XL: 8x4


def _ctrl(n):
    return [Action_entity(f"light.c{i}", "light", "on") for i in range(n)]


def Action_entity(eid, domain, state):
    from homedeck.ui.navigation import Action

    return Action(ActionKind.ENTITY, entity=DeviceEntity(eid, eid, domain, state))


def _sensors(n):
    return [Action_entity(f"sensor.s{i}", "sensor", "1") for i in range(n)]


def _entity_keys(result):
    return {k: a for k, a in result.items() if a.kind is ActionKind.ENTITY}


def test_back_key_reserved():
    result = layout_room(_ctrl(3), _sensors(2), TOTAL, COLS, page=0)
    assert result[0].kind is ActionKind.BACK


def test_controls_in_top_rows_sensors_in_bottom_row():
    result = layout_room(_ctrl(3), _sensors(2), TOTAL, COLS, page=0)
    control_keys = [k for k, a in result.items() if a.kind is ActionKind.ENTITY and a.entity.domain == "light"]
    sensor_keys = [k for k, a in result.items() if a.kind is ActionKind.ENTITY and a.entity.domain == "sensor"]

    # controls right after Back, in the top row
    assert control_keys == [1, 2, 3]
    # 2 sensors -> 1 bottom row (row 3 = keys 24..31), left-aligned
    assert sensor_keys == [24, 25]


def test_sensors_always_occupy_the_last_rows():
    # 10 sensors -> 2 rows; bottom band = rows 2 and 3 (keys 16..31)
    result = layout_room(_ctrl(2), _sensors(10), TOTAL, COLS, page=0)
    sensor_keys = sorted(k for k, a in result.items() if a.entity and a.entity.domain == "sensor")
    assert min(sensor_keys) >= 16
    assert sensor_keys == list(range(16, 26))
    # controls stay in the very top row
    control_keys = sorted(k for k, a in result.items() if a.entity and a.entity.domain == "light")
    assert control_keys == [1, 2]


def test_no_sensors_fills_controls_only():
    result = layout_room(_ctrl(5), [], TOTAL, COLS, page=0)
    control_keys = sorted(k for k, a in result.items() if a.entity)
    assert control_keys == [1, 2, 3, 4, 5]


def test_only_sensors_go_to_bottom():
    result = layout_room([], _sensors(3), TOTAL, COLS, page=0)
    sensor_keys = sorted(k for k, a in result.items() if a.entity)
    assert sensor_keys == [24, 25, 26]  # bottom row


def test_overflow_falls_back_to_paginated_sequential():
    # 31 controls + 8 sensors = 39 items > 32 keys -> pagination kicks in.
    result = layout_room(_ctrl(31), _sensors(8), TOTAL, COLS, page=0)
    # a Next page key must appear (sequential fallback), and Back is present
    assert result[0].kind is ActionKind.BACK
    assert any(a.kind is ActionKind.PAGE for a in result.values())


def test_navigation_room_view_places_sensors_at_bottom():
    room = Room("living", "Living", entities=[
        DeviceEntity("light.lamp", "Lamp", "light", "on"),
        DeviceEntity("switch.tv", "TV", "switch", "on"),
        DeviceEntity("sensor.temp", "Temp", "sensor", "21", attributes={"unit_of_measurement": "°C"}),
        DeviceEntity("binary_sensor.motion", "Motion", "binary_sensor", "off"),
        DeviceEntity("climate.thermostat", "Thermostat", "climate", "heat"),
    ])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    key_map = nav._build_key_map()

    readout_domains = {"sensor", "binary_sensor", "climate"}
    sensor_keys = [k for k, a in key_map.items() if a.entity and a.entity.domain in readout_domains]
    control_keys = [k for k, a in key_map.items() if a.entity and a.entity.domain not in readout_domains]

    # 3 read-only entities sit in the bottom row; 2 controls stay at the top.
    assert all(k >= 24 for k in sensor_keys)
    assert all(k < 24 for k in control_keys)
