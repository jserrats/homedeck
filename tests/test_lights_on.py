from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import (
    LIGHTS_ON_AREA,
    ActionKind,
    Frame,
    FrameKind,
    Navigation,
)


def _light(eid, state):
    return DeviceEntity(entity_id=eid, name=eid.split(".")[-1], domain="light", state=state)


def _rooms():
    living = Room("living", "Living", entities=[
        _light("light.lamp", "on"),
        _light("light.ceiling", "off"),
        DeviceEntity("switch.tv", "TV", "switch", "on"),  # not a light
    ])
    kitchen = Room("kitchen", "Kitchen", entities=[
        _light("light.counter", "on"),
        _light("light.broken", "unavailable"),
    ])
    return [living, kitchen]


def _nav():
    rooms = _rooms()
    display = ExportDisplay()
    return Navigation(display, KeyRenderer(display.key_size), rooms, on_service=lambda e: None), rooms


def _folder_light_ids(nav):
    """The light ids the dynamic folder would show, without rendering."""
    actions = nav._items_for(Frame(FrameKind.ROOM, room=nav.lights_on_room))
    return [a.entity.entity_id for a in actions]


def test_collect_on_lights_only_on_lights_sorted():
    nav, _ = _nav()
    ids = [e.entity_id for e in nav._collect_on_lights()]
    # only lights with status ON; switch.tv (on) and off/unavailable lights excluded;
    # sorted by name: "counter" < "lamp".
    assert ids == ["light.counter", "light.lamp"]


def test_lights_on_folder_pinned_to_bottom_row():
    nav, _ = _nav()
    key_map = nav._build_key_map()
    # 8x4 export grid: bottom row starts at key 24; Lights On is first there.
    assert key_map[24].kind is ActionKind.OPEN_ROOM
    assert key_map[24].room.area_id == LIGHTS_ON_AREA
    assert key_map[24].room.is_dynamic


def test_opening_folder_populates_current_on_lights():
    nav, _ = _nav()
    assert _folder_light_ids(nav) == ["light.counter", "light.lamp"]


def test_folder_membership_updates_when_a_light_toggles():
    nav, rooms = _nav()
    assert _folder_light_ids(nav) == ["light.counter", "light.lamp"]

    ceiling = next(e for r in rooms for e in r.entities if e.entity_id == "light.ceiling")
    ceiling.update_from_state("on", None)
    assert _folder_light_ids(nav) == ["light.ceiling", "light.counter", "light.lamp"]

    ceiling.update_from_state("off", None)
    assert "light.ceiling" not in _folder_light_ids(nav)
