"""Scripts: run-on-demand sequences, sharing the automations' page in a room.

`script.*` entities used to be dropped by the in-scope gate entirely. They now
live in their area's room (and only there — a script with no area is not shown),
alongside the automations on the room's last page(s). Unlike an automation there
is nothing to enable or disable, so the press is the run itself.
"""

import time

from homedeck.deck.icons import resolve_icon_name
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, Status, build_rooms
from homedeck.ui.navigation import Action, ActionKind, FrameKind, Navigation, layout_room

TOTAL, COLS = 32, 8  # Stream Deck XL: 8x4


def _action(eid, domain, state="off", attributes=None):
    return Action(ActionKind.ENTITY, entity=DeviceEntity(eid, eid, domain, state, attributes or {}))


def _ctrl(n):
    return [_action(f"light.c{i}", "light", "on") for i in range(n)]


def _sensors(n):
    return [_action(f"sensor.s{i}", "sensor", "21") for i in range(n)]


def _scripts(n):
    return [_action(f"script.s{i}", "script") for i in range(n)]


def _ids(key_map, domain):
    return [a.entity.entity_id for a in key_map.values()
            if a.kind is ActionKind.ENTITY and a.entity.domain == domain]


def _iso(epoch):
    from datetime import datetime, timezone

    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


# -- the model ----------------------------------------------------------------

def test_scripts_land_in_their_area():
    areas = [{"area_id": "office", "name": "Office"}]
    entities = [{"entity_id": "script.focus_mode", "area_id": "office", "name": "Focus Mode"}]
    states = {"script.focus_mode": {"state": "off", "attributes": {}}}
    rooms = build_rooms(areas, entities, [], states)
    assert [e.entity_id for e in rooms[0].entities] == ["script.focus_mode"]
    assert rooms[0].entities[0].is_script is True
    assert rooms[0].entities[0].is_routine is True


def test_a_script_with_no_area_is_not_shown():
    areas = [{"area_id": "office", "name": "Office"}]
    entities = [{"entity_id": "script.homeless", "name": "Homeless"}]
    states = {"script.homeless": {"state": "off", "attributes": {}}}
    assert build_rooms(areas, entities, [], states) == []


def test_running_reads_as_on_without_an_off_bar():
    running = DeviceEntity("script.a", "A", "script", "on")
    idle = DeviceEntity("script.b", "B", "script", "off")
    assert (running.status, idle.status) == (Status.ON, Status.OFF)
    # Idle is a script's resting state, not "switched off": no off-bar either way.
    assert (running.is_off, idle.is_off) == (False, False)
    assert DeviceEntity("script.c", "C", "script", "unavailable").status is Status.UNAVAILABLE


def test_tile_shows_when_it_last_ran_or_that_it_is_running():
    recent = DeviceEntity("script.a", "A", "script", "off",
                          {"last_triggered": _iso(time.time() - 300)})
    assert recent.display_value() == "5m ago"
    assert DeviceEntity("script.b", "B", "script", "off").display_value() == "Never"
    # A run in progress outranks the last-run time.
    assert DeviceEntity("script.c", "C", "script", "on",
                        {"last_triggered": _iso(time.time() - 300)}).display_value() == "Running"
    assert DeviceEntity("script.d", "D", "script", "unavailable").display_value() == "—"


def test_press_runs_it_and_cancels_a_run_in_progress():
    idle = DeviceEntity("script.a", "A", "script", "off")
    running = DeviceEntity("script.a", "A", "script", "on")
    assert idle.service_call() == ("script", "turn_on", "script.a", {})
    assert running.service_call() == ("script", "turn_off", "script.a", {})
    assert (idle.script_is_running, running.script_is_running) == (False, True)
    assert idle.script_run_call() == ("script", "turn_on", "script.a", {})
    assert idle.script_cancel_call() == ("script", "turn_off", "script.a", {})


def test_icon_follows_the_running_state():
    assert resolve_icon_name("script", None, None, state="on") == "script-text-play"
    assert resolve_icon_name("script", None, None, state="off") == "script-text"
    assert resolve_icon_name("script", None, "mdi:coffee", state="off") == "coffee"


# -- the room layout ----------------------------------------------------------

def test_scripts_stay_off_the_device_page():
    key_map = layout_room(_ctrl(3), _sensors(4), TOTAL, COLS, page=0, routines=_scripts(2))
    assert _ids(key_map, "script") == []
    assert _ids(key_map, "light") == [f"light.c{i}" for i in range(3)]


def test_scripts_own_the_last_page():
    key_map = layout_room(_ctrl(3), _sensors(4), TOTAL, COLS, page=1, routines=_scripts(2))
    assert key_map[0].kind is ActionKind.BACK
    assert _ids(key_map, "script") == ["script.s0", "script.s1"]
    assert key_map[31].data == {"page": 1, "count": 2, "cycle": True}


def test_scripts_share_the_page_with_automations():
    """One routine page holds both, in the room's own entity order."""
    nav, _ = _room_nav([
        DeviceEntity("light.lamp", "Lamp", "light", "on"),
        DeviceEntity("automation.a", "A", "automation", "on"),
        DeviceEntity("script.s", "S", "script", "off"),
    ])
    nav.handle_press(31, pressed=True)  # onto the routines page
    nav.handle_press(31, pressed=False)
    assert _ids(nav.key_map, "automation") == ["automation.a"]
    assert _ids(nav.key_map, "script") == ["script.s"]
    assert _ids(nav.key_map, "light") == []


# -- end to end ---------------------------------------------------------------

def _room_nav(entities, calls=None):
    room = Room("living", "Living", entities=entities)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=(calls.append if calls is not None else (lambda c: None)))
    nav.open_room(room)
    return nav, room


def _script_key(nav):
    return next(k for k, a in nav.key_map.items()
                if a.kind is ActionKind.ENTITY and a.entity.is_script)


def _button(nav, label):
    return next((k for k, a in nav.key_map.items()
                 if a.kind is ActionKind.SERVICE_BUTTON and a.data["label"] == label), None)


def _open_routines(nav):
    nav.handle_press(31, pressed=True)
    nav.handle_press(31, pressed=False)


def test_press_runs_it_hold_opens_the_controls():
    calls = []
    entity = DeviceEntity("script.movie_night", "Movie Night", "script", "off")
    nav, _ = _room_nav([DeviceEntity("light.lamp", "Lamp", "light", "on"), entity], calls)
    _open_routines(nav)
    key = _script_key(nav)

    nav.handle_press(key, pressed=True)
    nav.handle_press(key, pressed=False)
    assert calls == [("script", "turn_on", "script.movie_night", {})]

    nav.handle_press(key, pressed=True)
    time.sleep(0.6)  # past LONG_PRESS_S
    nav.handle_press(key, pressed=False)
    assert nav.stack[-1].kind is FrameKind.SCRIPT
    assert nav.stack[-1].entity is entity

    run = _button(nav, "Run")
    nav.handle_press(run, pressed=True)
    nav.handle_press(run, pressed=False)
    assert calls[-1] == ("script", "turn_on", "script.movie_night", {})
    assert nav.stack[-1].kind is FrameKind.SCRIPT  # Run stays in the view


def test_cancel_only_appears_while_it_is_running():
    calls = []
    entity = DeviceEntity("script.movie_night", "Movie Night", "script", "off")
    nav, _ = _room_nav([DeviceEntity("light.lamp", "Lamp", "light", "on"), entity], calls)
    _open_routines(nav)
    key = _script_key(nav)
    nav.handle_press(key, pressed=True)
    time.sleep(0.6)
    nav.handle_press(key, pressed=False)
    assert _button(nav, "Cancel") is None

    entity.state = "on"  # the run started; the view rebuilds on the state event
    nav.render()
    cancel = _button(nav, "Cancel")
    nav.handle_press(cancel, pressed=True)
    nav.handle_press(cancel, pressed=False)
    assert calls[-1] == ("script", "turn_off", "script.movie_night", {})
    assert nav.stack[-1].kind is FrameKind.SCRIPT
