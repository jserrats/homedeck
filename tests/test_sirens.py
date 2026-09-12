"""Sirens: an in-scope domain, gathered into the Security folder with the alarms.

`siren.*` entities used to be dropped by the in-scope gate entirely. They now
live in their area's room like any other control, and the Security folder pulls
them into a column of their own right after the alarm panels.
"""

from homedeck.deck.icons import resolve_icon_name
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import IN_SCOPE_DOMAINS, DeviceEntity, Room, Status, build_rooms
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

COLS = 8


def _ent(eid, domain, state, device_class=None):
    return DeviceEntity(eid, eid.split(".")[-1], domain, state,
                        attributes={"device_class": device_class} if device_class else {},
                        device_class=device_class)


def _nav(entities, calls=None):
    display = ExportDisplay()
    return Navigation(display, KeyRenderer(display.key_size), [Room("hall", "Hall", entities=entities)],
                      on_service=(calls.append if calls is not None else (lambda c: None)))


# -- the model ----------------------------------------------------------------

def test_sirens_are_in_scope_and_land_in_their_area():
    assert "siren" in IN_SCOPE_DOMAINS
    areas = [{"area_id": "hall", "name": "Hall"}]
    entities = [{"entity_id": "siren.alarm", "area_id": "hall", "name": "Alarm"}]
    states = {"siren.alarm": {"state": "off", "attributes": {}}}
    rooms = build_rooms(areas, entities, [], states)
    assert [e.entity_id for e in rooms[0].entities] == ["siren.alarm"]
    assert rooms[0].entities[0].is_siren is True


def test_sounding_reads_as_an_alert_not_a_plain_on():
    sounding = _ent("siren.a", "siren", "on")
    quiet = _ent("siren.b", "siren", "off")
    # Orange (the alarm/open palette), not the amber a light or switch gets.
    assert sounding.status is Status.OPEN
    assert quiet.status is Status.OFF
    assert _ent("siren.c", "siren", "unavailable").status is Status.UNAVAILABLE
    # Quiet is a siren's resting state, not "switched off": no off-bar.
    assert (sounding.is_off, quiet.is_off) == (False, False)


def test_press_toggles_the_siren():
    assert _ent("siren.a", "siren", "off").service_call() == ("siren", "toggle", "siren.a", {})
    assert _ent("siren.a", "siren", "on").service_call() == ("siren", "toggle", "siren.a", {})


def test_icon_follows_the_sounding_state():
    assert resolve_icon_name("siren", None, None, state="on") == "bullhorn"
    assert resolve_icon_name("siren", None, None, state="off") == "bullhorn-outline"
    assert resolve_icon_name("siren", None, "mdi:bell-ring", state="off") == "bell-ring"


# -- the Security folder -------------------------------------------------------

def _security_nav():
    return _nav([
        _ent("light.lamp", "light", "on"),                        # not security
        _ent("alarm_control_panel.house", "alarm_control_panel", "armed_away"),
        _ent("siren.outdoor", "siren", "off"),
        _ent("siren.indoor", "siren", "on"),
        _ent("lock.front", "lock", "locked"),
        _ent("binary_sensor.front_door", "binary_sensor", "on", "door"),
        _ent("binary_sensor.hall_motion", "binary_sensor", "off", "motion"),
    ])


def test_sirens_follow_the_alarms_in_the_security_groups():
    groups = _security_nav()._collect_security_groups()
    assert [[e.entity_id for e in g] for g in groups] == [
        ["alarm_control_panel.house"],
        ["siren.indoor", "siren.outdoor"],   # sorted by name, straight after the alarms
        ["lock.front"],
        ["binary_sensor.front_door"],
        ["binary_sensor.hall_motion"],
    ]


def test_sirens_get_a_column_of_their_own():
    nav = _security_nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SECURITY)]
    key_map = nav._build_key_map()

    def cols_for(predicate):
        return {k % COLS for k, a in key_map.items()
                if a.kind is ActionKind.ENTITY and predicate(a.entity)}

    siren_cols = cols_for(lambda e: e.is_siren)
    others = cols_for(lambda e: not e.is_siren)
    assert siren_cols and siren_cols.isdisjoint(others)
    assert min(siren_cols) >= 1  # right of the Back column


def test_pressing_a_siren_in_the_folder_toggles_it():
    calls = []
    nav = _nav([_ent("siren.outdoor", "siren", "off")], calls)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SECURITY)]
    nav.render()
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.is_siren)
    nav.handle_press(key, pressed=True)
    nav.handle_press(key, pressed=False)
    assert calls == [("siren", "toggle", "siren.outdoor", {})]


def test_a_siren_also_shows_among_its_room_controls():
    nav = _nav([_ent("light.lamp", "light", "on"), _ent("siren.outdoor", "siren", "off")])
    nav.open_room(nav.rooms[0])
    assert [a.entity.entity_id for a in nav.key_map.values()
            if a.kind is ActionKind.ENTITY] == ["light.lamp", "siren.outdoor"]
