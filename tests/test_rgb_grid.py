import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

TOTAL = 32
requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _rgb_light(state="on"):
    return DeviceEntity("light.rgb", "RGB", "light", state,
                        attributes={"supported_color_modes": ["hs", "color_temp"], "brightness": 200})


def _temp_light(state="on"):
    return DeviceEntity("light.ct", "CT", "light", state,
                        attributes={"supported_color_modes": ["color_temp"]})


# -- model --------------------------------------------------------------------

def test_rgb_detection():
    assert _rgb_light().supports_rgb_color is True
    assert _temp_light().supports_rgb_color is False


# -- menu ---------------------------------------------------------------------

def _menu(entity):
    room = Room("hall", "Hall", entities=[entity])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ENTITY_MENU, entity=entity)]
    return nav._build_key_map()


def test_rgb_menu_includes_color():
    targets = [a.data["target"] for a in _menu(_rgb_light()).values() if a.kind is ActionKind.MENU_ITEM]
    assert targets == ["toggle", "brightness", "color", "temperature", "history"]


def test_temp_only_menu_has_no_color():
    targets = [a.data["target"] for a in _menu(_temp_light()).values() if a.kind is ActionKind.MENU_ITEM]
    assert targets == ["toggle", "brightness", "temperature", "history"]


# -- color picker -------------------------------------------------------------

def _color_picker(entity):
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.PICKER, entity=entity, data={"type": "color"})]
    return nav


def test_color_picker_uses_hs_cells():
    view = _color_picker(_rgb_light())._build_key_map()
    assert view[0].kind is ActionKind.BACK
    cells = [a for k, a in sorted(view.items()) if a.kind is ActionKind.PICKER_CELL]
    assert len(cells) == TOTAL - 1
    assert all("hs_color" in a.data["call"][3] for a in cells)
    hues = [a.data["call"][3]["hs_color"][0] for a in cells]
    assert hues == sorted(hues)  # sweeps around the wheel


@requires_assets
def test_pressing_color_cell_calls_turn_on_with_hs_color():
    calls = []
    nav = _color_picker(_rgb_light())
    nav.on_service = calls.append
    nav.key_map = nav._build_key_map()
    call = nav.key_map[9].data["call"]
    nav.handle_press(9, True)
    assert calls == [call]
    assert "hs_color" in call[3]
    assert nav.stack[-1].kind is FrameKind.HOME  # applied and closed
