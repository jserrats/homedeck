"""Automations: a first-class domain, on a page of its own at the end of a room.

`automation.*` entities used to be dropped by the in-scope gate entirely. They
now live in their area's room like any other entity, but claim the room's last
page(s) instead of mixing into the devices or the sensor band.
"""

from homedeck.deck.icons import resolve_icon_name
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, Status, build_rooms
from homedeck.ui.navigation import Action, ActionKind, FrameKind, Navigation, layout_room

TOTAL, COLS = 32, 8  # Stream Deck XL: 8x4


def _action(eid, domain, state="on", attributes=None):
    return Action(ActionKind.ENTITY, entity=DeviceEntity(eid, eid, domain, state, attributes or {}))


def _ctrl(n):
    return [_action(f"light.c{i}", "light") for i in range(n)]


def _sensors(n):
    return [_action(f"sensor.s{i}", "sensor", "21") for i in range(n)]


def _autos(n):
    return [_action(f"automation.a{i}", "automation") for i in range(n)]


def _page_keys(key_map):
    return {k: a for k, a in key_map.items() if a.kind is ActionKind.PAGE}


def _ids(key_map, domain):
    return [a.entity.entity_id for a in key_map.values()
            if a.kind is ActionKind.ENTITY and a.entity.domain == domain]


# -- the model ----------------------------------------------------------------

def test_automations_land_in_their_area():
    areas = [{"area_id": "office", "name": "Office"}]
    entities = [{"entity_id": "automation.wake_up", "area_id": "office", "name": "Wake Up"}]
    states = {"automation.wake_up": {"state": "on", "attributes": {}}}
    rooms = build_rooms(areas, entities, [], states)
    assert [e.entity_id for e in rooms[0].entities] == ["automation.wake_up"]
    assert rooms[0].entities[0].is_automation is True


def test_enabled_and_disabled_read_like_a_switch():
    on = DeviceEntity("automation.a", "A", "automation", "on")
    off = DeviceEntity("automation.b", "B", "automation", "off")
    assert (on.status, off.status) == (Status.ON, Status.OFF)
    assert (on.is_off, off.is_off) == (False, True)  # disabled gets the off-bar


def _iso(epoch):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def test_tile_shows_when_it_last_ran():
    import time

    recent = DeviceEntity("automation.a", "A", "automation", "on",
                          {"last_triggered": _iso(time.time() - 300)})
    assert recent.last_triggered is not None
    assert recent.display_value() == "5m ago"
    assert DeviceEntity("automation.b", "B", "automation", "on").display_value() == "Never"
    assert DeviceEntity("automation.c", "C", "automation", "on",
                        {"last_triggered": None}).display_value() == "Never"
    # Unavailable: no attributes to read a last-run time from.
    assert DeviceEntity("automation.d", "D", "automation", "unavailable").display_value() == "—"


def test_press_toggles_and_run_triggers():
    entity = DeviceEntity("automation.a", "A", "automation", "on")
    assert entity.service_call() == ("automation", "toggle", "automation.a", {})
    assert entity.automation_trigger_call() == (
        "automation", "trigger", "automation.a", {"skip_condition": True})


def test_icon_follows_enabled_state():
    assert resolve_icon_name("automation", None, None, state="on") == "robot"
    assert resolve_icon_name("automation", None, None, state="off") == "robot-off"
    assert resolve_icon_name("automation", None, "mdi:weather-sunset", state="off") == "weather-sunset"


# -- the room layout ----------------------------------------------------------

def test_automations_stay_off_the_device_page():
    key_map = layout_room(_ctrl(3), _sensors(4), TOTAL, COLS, page=0, routines=_autos(2))
    assert _ids(key_map, "automation") == []
    assert _ids(key_map, "light") == [f"light.c{i}" for i in range(3)]
    assert len(_ids(key_map, "sensor")) == 4


def test_a_fitting_room_still_gets_a_page_key_for_its_automations():
    key_map = layout_room(_ctrl(3), _sensors(4), TOTAL, COLS, page=0, routines=_autos(2))
    assert set(_page_keys(key_map)) == {31}
    assert key_map[31].data == {"page": 0, "count": 2, "cycle": True}


def test_automations_own_the_last_page():
    key_map = layout_room(_ctrl(3), _sensors(4), TOTAL, COLS, page=1, routines=_autos(2))
    assert key_map[0].kind is ActionKind.BACK
    assert _ids(key_map, "automation") == ["automation.a0", "automation.a1"]
    assert _ids(key_map, "light") == [] and _ids(key_map, "sensor") == []
    assert key_map[31].data == {"page": 1, "count": 2, "cycle": True}


def test_automations_follow_the_band_pages():
    """A sensor-heavy room pages its band first, then reaches the automations."""
    pages = [layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=p, routines=_autos(2))
             for p in range(4)]
    assert [len(_ids(p, "sensor")) for p in pages] == [23, 23, 4, 0]  # 3 band pages, then autos
    assert _ids(pages[3], "automation") == ["automation.a0", "automation.a1"]
    for key_map in pages:
        assert key_map[31].data["count"] == 4


def test_more_automations_than_one_page_holds():
    seen = set()
    for page in range(1, 4):
        key_map = layout_room(_ctrl(2), [], TOTAL, COLS, page=page, routines=_autos(70))
        assert key_map[31].data == {"page": page, "count": 4, "cycle": True}
        seen |= set(_ids(key_map, "automation"))
    assert seen == {f"automation.a{i}" for i in range(70)}  # 30 + 30 + 10, nothing stranded


def test_page_clamped_to_the_last_automation_page():
    high = layout_room(_ctrl(2), _sensors(4), TOTAL, COLS, page=99, routines=_autos(2))
    last = layout_room(_ctrl(2), _sensors(4), TOTAL, COLS, page=1, routines=_autos(2))
    assert _ids(high, "automation") == _ids(last, "automation")


def test_dense_room_falls_back_to_a_flat_list_keeping_automations():
    # 31 controls leave no row for a band: flat Prev/Next list, nothing lost.
    key_map = layout_room(_ctrl(31), _sensors(8), TOTAL, COLS, page=0, routines=_autos(2))
    seen = set()
    for page in range(4):
        seen |= set(_ids(layout_room(_ctrl(31), _sensors(8), TOTAL, COLS, page=page,
                                     routines=_autos(2)), "automation"))
    assert key_map[0].kind is ActionKind.BACK
    assert seen == {"automation.a0", "automation.a1"}


# -- end to end ---------------------------------------------------------------

def _room_nav(entities, calls=None):
    room = Room("living", "Living", entities=entities)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=(calls.append if calls is not None else (lambda c: None)))
    nav.open_room(room)
    return nav, room


def test_cycling_reaches_the_automations_and_wraps_back():
    nav, _ = _room_nav(
        [DeviceEntity("light.lamp", "Lamp", "light", "on")]
        + [DeviceEntity(f"sensor.s{i}", f"S{i}", "sensor", "21") for i in range(4)]
        + [DeviceEntity(f"automation.a{i}", f"A{i}", "automation", "on") for i in range(3)]
    )
    assert _ids(nav.key_map, "automation") == []  # page 0 is the devices

    nav.handle_press(31, pressed=True)
    nav.handle_press(31, pressed=False)
    assert _ids(nav.key_map, "automation") == [f"automation.a{i}" for i in range(3)]
    assert _ids(nav.key_map, "light") == []

    nav.handle_press(31, pressed=True)  # wraps back to the devices
    nav.handle_press(31, pressed=False)
    assert nav.stack[-1].page == 0
    assert _ids(nav.key_map, "light") == ["light.lamp"]
    assert nav.stack[-1].kind is FrameKind.ROOM  # paging never pushes a frame


def test_press_toggles_hold_opens_the_controls():
    calls = []
    entity = DeviceEntity("automation.a0", "A0", "automation", "on")
    nav, _ = _room_nav([DeviceEntity("light.lamp", "Lamp", "light", "on"), entity], calls)
    nav.handle_press(31, pressed=True)  # onto the automations page
    nav.handle_press(31, pressed=False)
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.is_automation)

    nav.handle_press(key, pressed=True)
    nav.handle_press(key, pressed=False)
    assert calls == [("automation", "toggle", "automation.a0", {})]

    import time

    nav.handle_press(key, pressed=True)
    time.sleep(0.6)  # past LONG_PRESS_S
    nav.handle_press(key, pressed=False)
    assert nav.stack[-1].kind is FrameKind.AUTOMATION
    assert nav.stack[-1].entity is entity

    run = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.SERVICE_BUTTON)
    nav.handle_press(run, pressed=True)
    nav.handle_press(run, pressed=False)
    assert calls[-1] == ("automation", "trigger", "automation.a0", {"skip_condition": True})
    assert nav.stack[-1].kind is FrameKind.AUTOMATION  # Run stays in the view
