from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, group_by_floor
from homedeck.ui.navigation import ActionKind, Navigation


def _room(area_id, name, floor_id=None):
    return Room(area_id, name, floor_id=floor_id, entities=[
        DeviceEntity(f"light.{area_id}", name, "light", "on"),
    ])


def _floor_registry():
    # Intentionally out of order to verify sorting by level.
    return [
        {"floor_id": "first", "name": "First Floor", "level": 1},
        {"floor_id": "ground", "name": "Ground Floor", "level": 0},
    ]


def test_group_by_floor_orders_and_assigns():
    rooms = [
        _room("kitchen", "Kitchen", "ground"),
        _room("hall", "Hall", "ground"),
        _room("bed", "Bedroom", "first"),
        _room("garage", "Garage", None),  # no floor
        _room("attic", "Attic", "missing-floor"),  # floor not in registry
    ]
    floors, unassigned = group_by_floor(_floor_registry(), rooms)

    # floors sorted by level (ground=0 before first=1)
    assert [f.name for f in floors] == ["Ground Floor", "First Floor"]
    # rooms within a floor sorted by name
    assert [r.name for r in floors[0].rooms] == ["Hall", "Kitchen"]
    assert [r.name for r in floors[1].rooms] == ["Bedroom"]
    # rooms with no/unknown floor are unassigned, sorted by name
    assert [r.name for r in unassigned] == ["Attic", "Garage"]


def test_group_by_floor_empty_registry_all_unassigned():
    rooms = [_room("a", "A", "ground"), _room("b", "B")]
    floors, unassigned = group_by_floor([], rooms)
    assert floors == []
    assert [r.name for r in unassigned] == ["A", "B"]


def test_home_groups_rooms_under_floor_headers():
    rooms = [
        _room("kitchen", "Kitchen", "ground"),
        _room("bed", "Bedroom", "first"),
        _room("garage", "Garage", None),
    ]
    floors, unassigned = group_by_floor(_floor_registry(), rooms)

    display = ExportDisplay()
    nav = Navigation(
        display, KeyRenderer(display.key_size), rooms,
        on_service=lambda e: None, floors=floors, unassigned_rooms=unassigned,
    )
    key_map = nav._build_key_map()
    sequence = [key_map[k] for k in sorted(key_map)]

    labels = [
        (a.kind.name, (a.floor.name if a.floor else a.room.name if a.room else None))
        for a in sequence
    ]
    # Floor-grouped rooms fill the top rows; the special folders are pinned to
    # the bottom row (keys 24, 25), so they sort last.
    assert labels == [
        ("FLOOR_HEADER", "Ground Floor"),
        ("OPEN_ROOM", "Kitchen"),
        ("FLOOR_HEADER", "First Floor"),
        ("OPEN_ROOM", "Bedroom"),
        ("FLOOR_HEADER", "Other"),
        ("OPEN_ROOM", "Garage"),
        ("OPEN_ROOM", "Lights On"),
        ("OPEN_SECURITY", "Security"),
    ]


def test_floor_header_is_not_interactive():
    rooms = [_room("kitchen", "Kitchen", "ground")]
    floors, unassigned = group_by_floor(_floor_registry(), rooms)
    display = ExportDisplay()
    nav = Navigation(
        display, KeyRenderer(display.key_size), rooms,
        on_service=lambda e: None, floors=floors, unassigned_rooms=unassigned,
    )
    nav.key_map = nav._build_key_map()
    # find the header key and press it; stack must not change (no drill-in)
    header_key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.FLOOR_HEADER)
    depth_before = len(nav.stack)
    nav.handle_press(header_key, pressed=True)
    assert len(nav.stack) == depth_before
