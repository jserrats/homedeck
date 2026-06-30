from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, Status
from homedeck.ui.navigation import LIGHTS_ON_AREA, ActionKind, Navigation, View


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


def test_collect_on_lights_only_on_lights_sorted():
    nav, _ = _nav()
    on = nav._collect_on_lights()
    ids = [e.entity_id for e in on]
    # only lights with status ON; switch.tv (on) and off/unavailable lights excluded;
    # sorted by name: "counter" < "lamp".
    assert ids == ["light.counter", "light.lamp"]


def test_lights_on_folder_is_first_on_home():
    nav, _ = _nav()
    key_map = nav._build_key_map()
    first = key_map[0]
    assert first.kind is ActionKind.OPEN_ROOM
    assert first.room.area_id == LIGHTS_ON_AREA
    assert first.room.is_dynamic


def test_opening_folder_populates_current_on_lights():
    nav, _ = _nav()
    nav.open_room(nav.lights_on_room)
    assert nav.view is View.ROOM
    ids = [e.entity_id for e in nav.current_room.entities]
    assert ids == ["light.counter", "light.lamp"]


def test_folder_membership_updates_when_a_light_toggles():
    nav, rooms = _nav()
    nav.open_room(nav.lights_on_room)
    assert len(nav.current_room.entities) == 2

    # Turn the ceiling light on; the shared entity object is what the folder reads.
    ceiling = next(e for r in rooms for e in r.entities if e.entity_id == "light.ceiling")
    ceiling.update_from_state("on", None)
    nav.refresh_entity("light.ceiling")

    ids = [e.entity_id for e in nav.current_room.entities]
    assert ids == ["light.ceiling", "light.counter", "light.lamp"]

    # Turn one off again -> it leaves the folder.
    ceiling.update_from_state("off", None)
    nav.refresh_entity("light.ceiling")
    assert "light.ceiling" not in [e.entity_id for e in nav.current_room.entities]
