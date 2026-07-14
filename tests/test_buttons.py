import pytest

from homedeck.deck import icons
from homedeck.ha.model import IN_SCOPE_DOMAINS, Status, build_rooms


def _button(entity_id="button.doorbell", state="unknown"):
    from homedeck.ha.model import DeviceEntity

    return DeviceEntity(entity_id, entity_id.split(".")[-1], entity_id.split(".")[0], state)


def test_button_domains_in_scope():
    assert "button" in IN_SCOPE_DOMAINS
    assert "input_button" in IN_SCOPE_DOMAINS


def test_button_is_controllable_and_presses():
    b = _button("button.doorbell")
    assert b.is_controllable is True
    assert b.service_call() == ("button", "press", "button.doorbell", {})
    ib = _button("input_button.reset")
    assert ib.service_call() == ("input_button", "press", "input_button.reset", {})


def test_button_not_toggle_or_display_or_off():
    b = _button()
    assert b.display_value() is None
    assert b.is_off is False            # no "off" bar for a stateless button
    assert b.long_press_call() is None  # no lock-style direct long action
    assert b.has_long_press is True     # long press opens the options menu (History)


def test_button_status_actionable_unless_unavailable():
    # unknown (never pressed) and timestamps are actionable, not "unavailable"
    assert _button(state="unknown").status is Status.ON
    assert _button(state="2026-07-01T10:00:00+00:00").status is Status.ON
    assert _button(state="unavailable").status is Status.UNAVAILABLE


def test_button_grouped_with_controls_in_a_room():
    areas = [{"area_id": "hall", "name": "Hall"}]
    entities = [
        {"entity_id": "button.doorbell", "area_id": "hall"},
        {"entity_id": "sensor.temp", "area_id": "hall"},
        # a diagnostic/config restart button stays hidden, like in the HA UI
        {"entity_id": "button.restart", "area_id": "hall", "entity_category": "config"},
    ]
    states = {
        "button.doorbell": {"state": "unknown", "attributes": {}},
        "sensor.temp": {"state": "21", "attributes": {"unit_of_measurement": "°C"}},
        "button.restart": {"state": "unknown", "attributes": {}},
    }
    rooms = build_rooms(areas, entities, [], states)
    ids = [e.entity_id for e in rooms[0].entities]
    assert "button.doorbell" in ids
    assert "button.restart" not in ids  # config-category button excluded


@pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")
def test_button_icon_defaults():
    assert icons.resolve_icon_name("button", None, None) == "gesture-tap-button"
    assert icons.resolve_icon_name("button", "restart", None) == "restart"
    assert icons.resolve_icon_name("button", None, "mdi:bell") == "bell"  # explicit wins
