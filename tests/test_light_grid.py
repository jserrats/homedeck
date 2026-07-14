import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

TOTAL = 32
requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _ct_light(state="on"):
    return DeviceEntity(
        "light.lamp", "Lamp", "light", state,
        attributes={
            "supported_color_modes": ["color_temp"],
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
            "brightness": 128,
        },
    )


def _plain_light():
    return DeviceEntity("light.plain", "Plain", "light", "on",
                        attributes={"supported_color_modes": ["onoff"]})


# -- capabilities -------------------------------------------------------------

def test_capability_flags():
    ct = _ct_light()
    assert ct.supports_brightness is True
    assert ct.supports_color_temp is True
    assert ct.supports_rgb_color is False
    assert ct.has_long_press is True
    assert _plain_light().supports_brightness is False


# -- menu ---------------------------------------------------------------------

def _nav(entity):
    room = Room("hall", "Hall", entities=[entity])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_logbook=lambda e: [])
    return nav, room


def test_light_menu_lists_toggle_brightness_temperature_history():
    nav, _ = _nav(_ct_light())
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=_ct_light())]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    targets = [a.data["target"] for a in view.values() if a.kind is ActionKind.MENU_ITEM]
    assert targets == ["toggle", "brightness", "temperature", "history"]


def test_plain_light_menu_is_toggle_and_history():
    # a plain on/off light is toggleable, so its menu has Toggle + History
    nav, _ = _nav(_plain_light())
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=_plain_light())]
    targets = [a.data["target"] for a in nav._build_key_map().values() if a.kind is ActionKind.MENU_ITEM]
    assert targets == ["toggle", "history"]


def test_menu_toggle_item_toggles_and_stays():
    calls = []
    light = _ct_light("on")
    nav, room = _nav(light)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room),
                 Frame(FrameKind.ENTITY_MENU, entity=light)]
    nav.key_map = nav._build_key_map()
    toggle_key = next(k for k, a in nav.key_map.items()
                      if a.kind is ActionKind.MENU_ITEM and a.data["target"] == "toggle")
    nav.handle_press(toggle_key, True)
    assert calls == [("light", "toggle", "light.lamp", {})]
    assert nav.stack[-1].kind is FrameKind.ENTITY_MENU  # stays in the menu


@requires_assets
def test_toggle_tile_reflects_status():
    on = _ct_light("on")
    off = _ct_light("off")
    r = KeyRenderer((96, 96))
    assert r.toggle_button(on).tobytes() != r.toggle_button(off).tobytes()  # status shown


@requires_assets
def test_toggle_tile_updates_after_state_change():
    light = _ct_light("on")
    nav, room = _nav(light)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=light)]
    nav.render()
    toggle_key = next(k for k, a in nav.key_map.items()
                      if a.kind is ActionKind.MENU_ITEM and a.data["target"] == "toggle")
    before = nav.display.images[toggle_key].tobytes()
    light.update_from_state("off", light.attributes)
    nav.refresh_entity("light.lamp")   # menu rebuilds -> toggle tile now shows "off"
    assert nav.display.images[toggle_key].tobytes() != before


@requires_assets
def test_long_press_opens_menu_short_press_toggles(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    nav, room = _nav(_ct_light("on"))
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)

    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)            # short -> toggle
    assert calls == [("light", "toggle", "light.lamp", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)            # long -> options menu
    assert nav.stack[-1].kind is FrameKind.ENTITY_MENU
    bri = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.MENU_ITEM and a.data["target"] == "brightness")
    nav.handle_press(bri, True)
    assert nav.stack[-1].kind is FrameKind.PICKER


# -- pickers ------------------------------------------------------------------

def _picker(entity, ptype):
    nav, _ = _nav(entity)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.PICKER, entity=entity, data={"type": ptype})]
    return nav


def test_brightness_picker_cells_ascend_10_to_100():
    view = _picker(_ct_light(), "brightness")._build_key_map()
    assert view[0].kind is ActionKind.BACK
    cells = [a for k, a in sorted(view.items()) if a.kind is ActionKind.PICKER_CELL]
    assert len(cells) == TOTAL - 1
    calls = [a.data["call"] for a in cells]
    assert all(c[0:2] == ("light", "turn_on") and "brightness_pct" in c[3] for c in calls)
    pcts = [c[3]["brightness_pct"] for c in calls]
    assert pcts == sorted(pcts) and pcts[0] == 10 and pcts[-1] == 100


def test_temperature_picker_spans_the_lights_range():
    view = _picker(_ct_light(), "temperature")._build_key_map()
    cells = [a for k, a in sorted(view.items()) if a.kind is ActionKind.PICKER_CELL]
    kelvins = [a.data["call"][3]["color_temp_kelvin"] for a in cells]
    assert kelvins == sorted(kelvins) and kelvins[0] == 2000 and kelvins[-1] == 6500


@requires_assets
def test_pressing_a_cell_applies_and_closes():
    calls = []
    nav = _picker(_ct_light(), "brightness")
    nav.on_service = calls.append
    nav.key_map = nav._build_key_map()
    call = nav.key_map[5].data["call"]
    nav.handle_press(5, True)
    assert calls == [call]
    assert nav.stack[-1].kind is FrameKind.HOME  # applied and closed the picker


@requires_assets
def test_picker_renders_without_assets_errors():
    nav = _picker(_ct_light(), "temperature")
    nav.render()  # every swatch renders
