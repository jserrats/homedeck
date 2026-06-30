from homedeck.ha.model import (
    DeviceEntity,
    Status,
    _format_number,
    build_rooms,
    resolve_area_id,
)


def _sensor(state, unit=None, device_class=None):
    attrs = {}
    if unit is not None:
        attrs["unit_of_measurement"] = unit
    if device_class is not None:
        attrs["device_class"] = device_class
    return DeviceEntity(
        entity_id="sensor.x", name="X", domain="sensor", state=state,
        attributes=attrs, device_class=device_class,
    )


def test_format_number_strips_float_noise():
    assert _format_number("78.40000000001") == "78.4"
    assert _format_number("21.4") == "21.4"
    assert _format_number("48") == "48"
    assert _format_number("48.0") == "48"
    assert _format_number("1234.56") == "1234.56"
    assert _format_number("12345.678") == "12345.68"  # rounded to 2 dp


def test_format_number_passes_through_non_numeric():
    assert _format_number("playing") == "playing"
    assert _format_number(None) == "None"


def test_display_value_spacing_and_units():
    assert _sensor("78.40000000001", "cm", "distance").display_value() == "78.4 cm"
    assert _sensor("48", "%").display_value() == "48%"            # percent attaches
    assert _sensor("21.4", "°C", "temperature").display_value() == "21.4 °C"
    assert _sensor("1234.5", "W", "power").display_value() == "1234.5 W"


def test_display_value_unavailable():
    s = _sensor("unavailable", "cm")
    assert s.display_value() == "—"


def test_resolve_area_id_direct():
    entry = {"entity_id": "light.x", "area_id": "kitchen", "device_id": "d1"}
    assert resolve_area_id(entry, {"d1": {"id": "d1", "area_id": "bedroom"}}) == "kitchen"


def test_resolve_area_id_device_fallback():
    entry = {"entity_id": "light.x", "area_id": None, "device_id": "d1"}
    assert resolve_area_id(entry, {"d1": {"id": "d1", "area_id": "bedroom"}}) == "bedroom"


def test_resolve_area_id_none():
    entry = {"entity_id": "light.x", "area_id": None, "device_id": None}
    assert resolve_area_id(entry, {}) is None


def _registries():
    areas = [
        {"area_id": "living", "name": "Living Room", "icon": "mdi:sofa"},
        {"area_id": "kitchen", "name": "Kitchen"},
        {"area_id": "empty", "name": "Empty Room"},
    ]
    devices = [{"id": "dev1", "area_id": "kitchen"}]
    entities = [
        {"entity_id": "light.lamp", "area_id": "living", "name": "Corner Lamp"},
        {"entity_id": "switch.fan", "area_id": "living"},
        {"entity_id": "sensor.temp", "area_id": "living"},
        # area via device fallback
        {"entity_id": "light.ceiling", "area_id": None, "device_id": "dev1"},
        # skipped: hidden / disabled / out of scope / no area / diagnostic / config
        {"entity_id": "light.hidden", "area_id": "living", "hidden_by": "user"},
        {"entity_id": "light.disabled", "area_id": "living", "disabled_by": "user"},
        {"entity_id": "media_player.tv", "area_id": "living"},
        {"entity_id": "light.orphan", "area_id": None, "device_id": None},
        {"entity_id": "sensor.rssi", "area_id": "living", "entity_category": "diagnostic"},
        {"entity_id": "switch.config", "area_id": "living", "entity_category": "config"},
    ]
    states = {
        "light.lamp": {"state": "on", "attributes": {"friendly_name": "Corner Lamp"}},
        "switch.fan": {"state": "off", "attributes": {}},
        "sensor.temp": {"state": "21.4", "attributes": {"unit_of_measurement": "°C", "device_class": "temperature"}},
        "light.ceiling": {"state": "unavailable", "attributes": {}},
    }
    return areas, entities, devices, states


def test_build_rooms_filters_and_groups():
    areas, entities, devices, states = _registries()
    rooms = build_rooms(areas, entities, devices, states)

    names = [r.name for r in rooms]
    # "Empty Room" has no entities and is dropped; rooms are alphabetical.
    assert names == ["Kitchen", "Living Room"]

    living = next(r for r in rooms if r.area_id == "living")
    living_ids = [e.entity_id for e in living.entities]
    # hidden/disabled/out-of-scope/orphan/diagnostic/config excluded; sorted by
    # friendly name: "Corner Lamp" < "Fan" < "Temp".
    assert living_ids == ["light.lamp", "switch.fan", "sensor.temp"]
    # diagnostic/config entities never appear
    assert "sensor.rssi" not in living_ids
    assert "switch.config" not in living_ids

    kitchen = next(r for r in rooms if r.area_id == "kitchen")
    assert [e.entity_id for e in kitchen.entities] == ["light.ceiling"]


def test_room_icon_passthrough():
    areas, entities, devices, states = _registries()
    rooms = build_rooms(areas, entities, devices, states)
    living = next(r for r in rooms if r.area_id == "living")
    assert living.icon == "mdi:sofa"


def test_status_and_value():
    areas, entities, devices, states = _registries()
    rooms = build_rooms(areas, entities, devices, states)
    by_id = {e.entity_id: e for r in rooms for e in r.entities}

    assert by_id["light.lamp"].status is Status.ON
    assert by_id["switch.fan"].status is Status.OFF
    assert by_id["light.ceiling"].status is Status.UNAVAILABLE

    # sensor shows value+unit (space before unit) and is not controllable
    temp = by_id["sensor.temp"]
    assert temp.display_value() == "21.4 °C"
    assert temp.is_controllable is False
    assert temp.service_call() is None

    # controllable device toggles, no display value
    lamp = by_id["light.lamp"]
    assert lamp.is_controllable is True
    assert lamp.display_value() is None
    assert lamp.service_call() == ("light", "toggle", "light.lamp")


def test_update_from_state():
    areas, entities, devices, states = _registries()
    rooms = build_rooms(areas, entities, devices, states)
    fan = next(e for r in rooms for e in r.entities if e.entity_id == "switch.fan")
    assert fan.status is Status.OFF
    fan.update_from_state("on", {"friendly_name": "Fan"})
    assert fan.status is Status.ON
