"""Home overflow: rooms past the first page must stay reachable.

The specials band can be full (weather + calendars enabled = 8 tiles on an
8-column deck), which used to leave no cell for pagination at all.
"""

from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.calendar import Calendar
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ha.weather import Weather
from homedeck.ui.navigation import Action, ActionKind, Navigation, layout_home

TOTAL, COLS = 32, 8


def _content(n):
    return [Action(ActionKind.OPEN_ROOM, room=None) for _ in range(n)]


def _specials(n):
    return [Action(ActionKind.OPEN_SECURITY) for _ in range(n)]


def _page_keys(key_map):
    return {k for k, a in key_map.items() if a.kind is ActionKind.PAGE}


def test_full_specials_band_still_gets_a_page_key():
    # 8 specials fill the whole bottom row, so the page key costs the last
    # content slot (key 23) instead.
    key_map = layout_home(_content(30), _specials(8), TOTAL, COLS, page=0)
    assert _page_keys(key_map) == {23}
    assert key_map[23].delta == 1
    assert key_map[23].data == {"page": 0, "count": 2, "cycle": True}
    assert all(key_map[k].kind is ActionKind.OPEN_ROOM for k in range(23))  # 23 rooms/page
    assert all(key_map[k].kind is ActionKind.OPEN_SECURITY for k in range(24, 32))  # band intact


def test_spare_band_cell_is_used_before_a_content_slot():
    # 7 specials leave key 31 free, so no room slot is spent on paging.
    key_map = layout_home(_content(30), _specials(7), TOTAL, COLS, page=0)
    assert _page_keys(key_map) == {31}
    assert all(key_map[k].kind is ActionKind.OPEN_ROOM for k in range(24))  # full 24 rooms/page


def test_single_page_home_has_no_page_key():
    key_map = layout_home(_content(24), _specials(8), TOTAL, COLS, page=0)
    assert _page_keys(key_map) == set()
    assert all(key_map[k].kind is ActionKind.OPEN_ROOM for k in range(24))


def test_page_key_is_present_on_every_page():
    # The old layout could draw Next without Prev, stranding the user forward.
    for page in range(3):
        key_map = layout_home(_content(60), _specials(8), TOTAL, COLS, page=page)
        assert _page_keys(key_map) == {23}
        assert key_map[23].data["page"] == page and key_map[23].data["count"] == 3


def test_portrait_full_band_gets_a_page_key():
    # Rotated deck: 4 columns, 8 rows -> two reserved rows, both full of specials.
    key_map = layout_home(_content(40), _specials(8), TOTAL, cols=4, page=0)
    assert _page_keys(key_map) == {23}
    assert all(key_map[k].kind is ActionKind.OPEN_ROOM for k in range(23))


def test_every_room_is_reachable_by_cycling():
    """End-to-end: press the page key round and round, see every room, wrap."""
    rooms = [
        Room(f"r{i}", f"Room {i}", entities=[DeviceEntity(f"light.r{i}", f"Room {i}", "light", "off")])
        for i in range(30)  # > 23 per page -> two pages
    ]
    display = ExportDisplay()
    nav = Navigation(
        display, KeyRenderer(display.key_size), rooms, on_service=lambda e: None,
        weather=Weather("weather.home", "sunny", 20.0),        # 8 specials: a full band
        calendars=[Calendar("calendar.home", "Home", None)],
    )
    nav.key_map = nav._build_key_map()
    page_key = next(iter(_page_keys(nav.key_map)))
    assert page_key == 23

    seen = set()
    for _ in range(4):  # twice around a two-page home
        seen |= {a.room.name for a in nav.key_map.values()
                 if a.kind is ActionKind.OPEN_ROOM and not a.room.is_dynamic}
        nav.handle_press(page_key, pressed=True)
        nav.handle_press(page_key, pressed=False)

    assert seen == {r.name for r in rooms}          # nothing stranded
    assert nav.stack[-1].page == 0                  # wrapped back around
    assert nav.stack[-1].kind.name == "HOME"        # paging never pushes a frame


def test_page_tile_renders_its_position():
    display = ExportDisplay()
    renderer = KeyRenderer(display.key_size)
    nav = Navigation(display, renderer, [], on_service=lambda e: None)
    action = Action(ActionKind.PAGE, delta=1, data={"page": 1, "count": 3, "cycle": True})
    # The caption ("2/3") replaces the label; check it renders rather than raising.
    assert nav._image_for(action).size == display.key_size
    assert renderer.nav("page", caption="2/3") != renderer.nav("page")


def test_collapsing_a_floor_resets_the_page():
    from homedeck.ha.model import group_by_floor

    rooms = [
        Room(f"r{i}", f"Room {i}", floor_id="ground",
             entities=[DeviceEntity(f"light.r{i}", f"Room {i}", "light", "off")])
        for i in range(30)
    ]
    floors, unassigned = group_by_floor([{"floor_id": "ground", "name": "Ground", "level": 0}], rooms)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), rooms, on_service=lambda e: None,
                     floors=floors, unassigned_rooms=unassigned,
                     weather=Weather("weather.home", "sunny", 20.0),
                     calendars=[Calendar("calendar.home", "Home", None)])
    nav.key_map = nav._build_key_map()
    nav.handle_press(23, pressed=True)  # page forward
    nav.handle_press(23, pressed=False)
    assert nav.stack[-1].page == 1

    nav._toggle_floor(floors[0])  # collapse: content shrinks to a single page
    assert nav.stack[-1].page == 0  # no stale index left behind
    assert _page_keys(nav.key_map) == set()  # and the page key is gone with it
