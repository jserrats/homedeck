from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import (
    SECURITY_AREA,
    Action,
    ActionKind,
    Frame,
    FrameKind,
    Navigation,
    layout_home,
    layout_security,
)


def test_layout_home_pins_specials_and_paginates_content():
    content = [Action(ActionKind.OPEN_ROOM, room=None) for _ in range(30)]  # > 24 top-row slots
    specials = [Action(ActionKind.OPEN_SECURITY), Action(ActionKind.OPEN_SECURITY)]
    page0 = layout_home(content, specials, 32, 8, page=0)
    # specials always bottom-left; Next on bottom-right
    assert page0[24].kind is ActionKind.OPEN_SECURITY
    assert page0[25].kind is ActionKind.OPEN_SECURITY
    assert page0[31].kind is ActionKind.PAGE and page0[31].delta == 1
    # top rows hold the first 24 content items
    assert all(page0[k].kind is ActionKind.OPEN_ROOM for k in range(24))

    page1 = layout_home(content, specials, 32, 8, page=1)
    assert page1[24].kind is ActionKind.OPEN_SECURITY  # still pinned
    assert page1[30].kind is ActionKind.PAGE and page1[30].delta == -1  # Prev

COLS, TOTAL = 8, 32


def _ent(eid, domain, state, device_class=None):
    return DeviceEntity(
        eid, eid.split(".")[-1], domain, state,
        attributes={"device_class": device_class} if device_class else {},
        device_class=device_class,
    )


def _nav():
    rooms = [Room("hall", "Hall", entities=[
        _ent("light.lamp", "light", "on"),                       # not security
        _ent("lock.front", "lock", "locked"),
        _ent("lock.back", "lock", "unlocked"),
        _ent("binary_sensor.front_door", "binary_sensor", "on", "door"),
        _ent("cover.garage", "cover", "closed", "garage"),
        _ent("binary_sensor.hall_motion", "binary_sensor", "off", "motion"),
        _ent("sensor.temp", "sensor", "21", None),               # not security
    ])]
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), rooms, on_service=lambda c: None)
    return nav


def test_special_folders_pinned_to_bottom_row():
    nav = _nav()
    home = nav._build_key_map()
    # bottom row of the 8x4 export grid starts at key 24
    assert home[24].kind is ActionKind.OPEN_ROOM and home[24].room.is_dynamic  # Lights On
    assert home[25].kind is ActionKind.OPEN_SECURITY
    assert home[25].room.area_id == SECURITY_AREA
    # the single room sits in the top rows, above the specials
    room_keys = [k for k, a in home.items() if a.kind is ActionKind.OPEN_ROOM and not a.room.is_dynamic]
    assert all(k < 24 for k in room_keys)


def test_security_groups_contents():
    nav = _nav()
    groups = nav._collect_security_groups()
    ids = [[e.entity_id for e in g] for g in groups]
    # locks, then closures (door sensor + garage cover), then presence; light/temp excluded
    assert ids == [
        ["lock.back", "lock.front"],
        ["binary_sensor.front_door", "cover.garage"],
        ["binary_sensor.hall_motion"],
    ]


def test_security_view_one_type_per_column():
    nav = _nav()
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SECURITY)]
    key_map = nav._build_key_map()

    assert key_map[0].kind is ActionKind.BACK

    def cols_for(predicate):
        return {k % COLS for k, a in key_map.items()
                if a.kind is ActionKind.ENTITY and predicate(a.entity)}

    lock_cols = cols_for(lambda e: e.domain == "lock")
    closure_cols = cols_for(lambda e: e.is_closure)
    presence_cols = cols_for(lambda e: e.is_presence)

    # each type occupies its own column(s); columns never overlap between types
    assert lock_cols and closure_cols and presence_cols
    assert lock_cols.isdisjoint(closure_cols)
    assert lock_cols.isdisjoint(presence_cols)
    assert closure_cols.isdisjoint(presence_cols)
    # content is to the right of the Back column (column 0)
    assert min(lock_cols | closure_cols | presence_cols) >= 1


def test_opening_security_pushes_frame():
    nav = _nav()
    nav.key_map = nav._build_key_map()
    sec_key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_SECURITY)
    nav.handle_press(sec_key, pressed=True)
    assert nav.stack[-1].kind is FrameKind.SECURITY


# -- layout_security unit tests ----------------------------------------------

def _actions(n):
    return [Action_entity(f"x{i}") for i in range(n)]


def Action_entity(name):
    from homedeck.ui.navigation import Action
    return Action(ActionKind.ENTITY, entity=DeviceEntity(f"lock.{name}", name, "lock", "locked"))


def test_layout_security_each_group_starts_new_column():
    # group A: 3 items -> column 1; group B: 2 items -> column 2
    result = layout_security([_actions(3), _actions(2)], TOTAL, COLS, page=0)
    counts: dict[int, int] = {}
    for k, a in result.items():
        if a.kind is ActionKind.ENTITY:
            counts[k % COLS] = counts.get(k % COLS, 0) + 1
    assert counts == {1: 3, 2: 2}  # group A in column 1, group B in column 2


def test_layout_security_group_wraps_within_its_own_columns():
    # 10 locks wrap across columns of 4 (rows=4) but stay contiguous
    result = layout_security([_actions(10)], TOTAL, COLS, page=0)
    entity_cols = sorted({k % COLS for k, a in result.items() if a.kind is ActionKind.ENTITY})
    assert entity_cols == [1, 2, 3]  # 4 + 4 + 2 across columns 1, 2, 3


def test_layout_security_paginates_when_too_many_columns():
    # 8 groups of 4 -> 8 columns, but only 7 content columns -> 2 pages
    groups = [_actions(4) for _ in range(8)]
    page0 = layout_security(groups, TOTAL, COLS, page=0)
    assert any(a.kind is ActionKind.PAGE and a.delta == 1 for a in page0.values())
    page1 = layout_security(groups, TOTAL, COLS, page=1)
    assert any(a.kind is ActionKind.PAGE and a.delta == -1 for a in page1.values())
