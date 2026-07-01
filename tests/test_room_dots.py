import pytest

from homedeck.deck import icons
from homedeck.deck import renderer as rmod
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import ActionKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _light(state):
    return DeviceEntity("light.l", "L", "light", state)


def _motion(state):
    return DeviceEntity("binary_sensor.m", "M", "binary_sensor", state,
                        attributes={"device_class": "motion"}, device_class="motion")


def _nav(room):
    display = ExportDisplay()
    return Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)


def _has_color(img, color, xf=0.35, yf=0.6):
    """Any pixel close to `color` in the top-left column (dots stack downward)?"""
    w, h = img.size
    for x in range(int(w * xf)):
        for y in range(int(h * yf)):
            r, g, b = img.getpixel((x, y))
            if abs(r - color[0]) < 30 and abs(g - color[1]) < 30 and abs(b - color[2]) < 30:
                return True
    return False


@requires_assets
def test_yellow_dot_when_a_light_is_on():
    room = Room("living", "Living", entities=[_light("on")])
    img = _nav(room)._render_room_tile(room)
    assert _has_color(img, rmod.DOT_LIGHT)
    assert not _has_color(img, rmod.DOT_PRESENCE)


@requires_assets
def test_no_dot_when_light_off():
    room = Room("living", "Living", entities=[_light("off")])
    img = _nav(room)._render_room_tile(room)
    assert not _has_color(img, rmod.DOT_LIGHT)


@requires_assets
def test_purple_dot_when_presence_detected():
    room = Room("hall", "Hall", entities=[_motion("on")])
    img = _nav(room)._render_room_tile(room)
    assert _has_color(img, rmod.DOT_PRESENCE)
    # motion "off" -> no dot
    room_off = Room("hall", "Hall", entities=[_motion("off")])
    assert not _has_color(_nav(room_off)._render_room_tile(room_off), rmod.DOT_PRESENCE)


@requires_assets
def test_both_dots_when_light_on_and_presence():
    room = Room("living", "Living", entities=[_light("on"), _motion("on")])
    img = _nav(room)._render_room_tile(room)
    assert _has_color(img, rmod.DOT_LIGHT)
    assert _has_color(img, rmod.DOT_PRESENCE)


@requires_assets
def test_special_folders_have_no_dots():
    # The Lights On folder is dynamic and must not get room dots even if lights are on.
    room = Room("living", "Living", entities=[_light("on")])
    nav = _nav(room)
    nav.render()  # home
    lights_key = next(k for k, a in nav.key_map.items()
                      if a.kind is ActionKind.OPEN_ROOM and a.room.is_dynamic)
    assert not _has_color(nav.display.images[lights_key], rmod.DOT_LIGHT)


@requires_assets
def test_home_updates_room_dot_on_light_change():
    light = _light("off")
    room = Room("living", "Living", entities=[light])
    nav = _nav(room)
    nav.render()
    room_key = next(k for k, a in nav.key_map.items()
                    if a.kind is ActionKind.OPEN_ROOM and not a.room.is_dynamic)
    assert not _has_color(nav.display.images[room_key], rmod.DOT_LIGHT)

    light.update_from_state("on", None)
    nav.refresh_entity("light.l")
    assert _has_color(nav.display.images[room_key], rmod.DOT_LIGHT)
