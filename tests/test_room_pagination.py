"""Room overflow: sensors past the first band page must stay reachable.

A sensor-heavy room used to drop its bottom band entirely and re-lay the whole
view as a flat Prev/Next list. The band now pages in place, and paging steps
from the page the key was drawn on, so a view whose content shrank underneath a
stored page index doesn't swallow the first press.
"""

from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import ActionKind, Navigation, layout_room

TOTAL, COLS = 32, 8  # Stream Deck XL: 8x4


def _action(eid, domain, state="on"):
    from homedeck.ui.navigation import Action

    return Action(ActionKind.ENTITY, entity=DeviceEntity(eid, eid, domain, state))


def _ctrl(n):
    return [_action(f"light.c{i}", "light") for i in range(n)]


def _sensors(n):
    return [_action(f"sensor.s{i}", "sensor", "21") for i in range(n)]


def _page_keys(key_map):
    return {k: a for k, a in key_map.items() if a.kind is ActionKind.PAGE}


def _ids(key_map, domain):
    return [a.entity.entity_id for a in key_map.values()
            if a.kind is ActionKind.ENTITY and a.entity.domain == domain]


def test_band_pages_in_place_instead_of_collapsing():
    key_map = layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=0)
    # Controls keep the top row, the band keeps rows 1..3 (keys 8..31).
    assert sorted(k for k in key_map if key_map[k].kind is ActionKind.ENTITY
                  and key_map[k].entity.domain == "light") == [1, 2]
    assert all(k >= 8 for k, a in key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.domain == "sensor")
    # The band's last cell pages it: 23 sensors per page, 50 sensors -> 3 pages.
    assert set(_page_keys(key_map)) == {31}
    assert key_map[31].delta == 1
    assert key_map[31].data == {"page": 0, "count": 3, "cycle": True}
    assert _ids(key_map, "sensor") == [f"sensor.s{i}" for i in range(23)]


def test_controls_stay_put_across_sensor_pages():
    """Paging the band never moves (or drops) the controllable devices."""
    pages = [layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=p) for p in range(3)]
    for key_map in pages:
        assert _ids(key_map, "light") == ["light.c0", "light.c1"]
        assert key_map[0].kind is ActionKind.BACK
    assert _ids(pages[2], "sensor") == [f"sensor.s{i}" for i in range(46, 50)]  # tail page


def test_every_sensor_appears_on_some_page():
    seen = set()
    for page in range(3):
        seen |= set(_ids(layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=page), "sensor"))
    assert seen == {f"sensor.s{i}" for i in range(50)}


def test_band_shrinks_to_the_rows_the_controls_leave():
    # 10 controls need two rows (Back included), so the band gets the other two.
    key_map = layout_room(_ctrl(10), _sensors(40), TOTAL, COLS, page=0)
    assert sorted(k for k, a in key_map.items()
                  if a.kind is ActionKind.ENTITY and a.entity.domain == "light") == list(range(1, 11))
    sensor_keys = sorted(k for k, a in key_map.items()
                         if a.kind is ActionKind.ENTITY and a.entity.domain == "sensor")
    assert sensor_keys == list(range(16, 31))  # rows 2..3, key 31 spent on paging
    assert key_map[31].data == {"page": 0, "count": 3, "cycle": True}


def test_page_clamped_to_the_last_band_page():
    high = layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=99)
    assert _ids(high, "sensor") == _ids(layout_room(_ctrl(2), _sensors(50), TOTAL, COLS, page=2), "sensor")


def test_fitting_band_has_no_page_key():
    key_map = layout_room(_ctrl(2), _sensors(24), TOTAL, COLS, page=0)
    assert _page_keys(key_map) == {}
    assert len(_ids(key_map, "sensor")) == 24


def test_controls_needing_every_row_still_fall_back_to_sequential():
    # 31 controls leave no row for a band: flat Prev/Next list, nothing lost.
    key_map = layout_room(_ctrl(31), _sensors(8), TOTAL, COLS, page=0)
    assert key_map[0].kind is ActionKind.BACK
    assert [a.delta for a in _page_keys(key_map).values()] == [1]  # Next only, on page 0
    assert 31 in _page_keys(key_map)


def _room_nav(entities):
    room = Room("living", "Living", entities=entities)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.open_room(room)
    return nav, room


def test_every_sensor_is_reachable_by_cycling():
    """End-to-end: press the band's page key round and round, see every sensor."""
    nav, _ = _room_nav(
        [DeviceEntity("light.lamp", "Lamp", "light", "on")]
        + [DeviceEntity(f"sensor.s{i}", f"S{i}", "sensor", "21") for i in range(50)]
    )
    page_key = next(iter(_page_keys(nav.key_map)))
    assert page_key == 31

    seen = set()
    for _ in range(6):  # twice around a three-page band
        seen |= set(_ids(nav.key_map, "sensor"))
        assert _ids(nav.key_map, "light") == ["light.lamp"]  # controls never scroll away
        nav.handle_press(page_key, pressed=True)
        nav.handle_press(page_key, pressed=False)

    assert seen == {f"sensor.s{i}" for i in range(50)}  # nothing stranded
    assert nav.stack[-1].page == 0                      # wrapped back around
    assert nav.stack[-1].kind.name == "ROOM"            # paging never pushes a frame


def test_paging_steps_from_the_page_on_screen():
    """Content shrinking under a stored page must not eat the next press.

    The dynamic "Lights On" folder rebuilds as lights toggle; the layout clamps
    the page it draws, so stepping from the frame's stale index would re-render
    the same page.
    """
    lights = [DeviceEntity(f"light.l{i}", f"L{i}", "light", "on") for i in range(70)]
    room = Room("living", "Living", entities=lights)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.open_room(nav.lights_on_room)  # 70 controls -> flat Prev/Next, three pages

    for _ in range(2):
        nav.handle_press(31, pressed=True)   # Next, twice -> last page
        nav.handle_press(31, pressed=False)
    assert nav.stack[-1].page == 2
    assert nav.key_map[30].data == {"page": 2, "count": 3}

    for light in lights[35:]:  # half of them turn off: membership shrinks to two pages
        light.state = "off"
    nav.refresh_entity("light.l40")
    assert nav.stack[-1].page == 2                          # frame index now stale...
    assert nav.key_map[30].data == {"page": 1, "count": 2}   # ...though page 1 is on screen

    nav.handle_press(30, pressed=True)  # a single Prev press must move
    nav.handle_press(30, pressed=False)
    assert nav.stack[-1].page == 0
    assert _ids(nav.key_map, "light")[0] == "light.l0"
