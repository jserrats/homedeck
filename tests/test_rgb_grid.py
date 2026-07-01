import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

COLS, ROWS, TOTAL = 8, 4, 32
requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _rgb_light(state="on"):
    return DeviceEntity("light.rgb", "RGB", "light", state,
                        attributes={"supported_color_modes": ["hs", "color_temp"]})


def _temp_light(state="on"):
    return DeviceEntity("light.ct", "CT", "light", state,
                        attributes={"supported_color_modes": ["color_temp"]})


# -- model --------------------------------------------------------------------

def test_rgb_detection_and_long_press():
    assert _rgb_light().supports_rgb_color is True
    assert _temp_light().supports_rgb_color is False
    assert _rgb_light().has_long_press is True


def test_color_grid_levels():
    bri, hues = _rgb_light().color_grid_levels(ROWS, COLS)
    assert bri == [10, 40, 70, 100]
    assert hues == [0, 45, 90, 135, 180, 225, 270, 315]  # around the wheel


# -- layout -------------------------------------------------------------------

def _grid(entity):
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=entity)]
    return nav._build_key_map()


def test_rgb_light_grid_uses_hue_cells():
    key_map = _grid(_rgb_light())
    cells = [a for a in key_map.values() if a.kind is ActionKind.GRID_CELL]
    assert len(cells) == TOTAL
    assert all("hs_color" in a.data for a in cells)         # color picker
    assert all("color_temp_kelvin" not in a.data for a in cells)
    # top row brightest, hue increases across the columns
    top = _grid(_rgb_light())
    row0 = [top[c].data["hs_color"][0] for c in range(COLS)]
    assert row0 == sorted(row0)
    assert {top[c].data["brightness_pct"] for c in range(COLS)} == {100}


def test_temp_only_light_still_uses_kelvin_cells():
    cells = [a for a in _grid(_temp_light()).values() if a.kind is ActionKind.GRID_CELL]
    assert all("color_temp_kelvin" in a.data for a in cells)
    assert all("hs_color" not in a.data for a in cells)


# -- behavior -----------------------------------------------------------------

@requires_assets
def test_pressing_rgb_cell_calls_turn_on_with_hs_color():
    calls = []
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=calls.append)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_rgb_light())]
    nav.key_map = nav._build_key_map()

    data = nav.key_map[9].data
    nav.handle_press(9, True)
    assert calls == [("light", "turn_on", "light.rgb", data)]
    assert "hs_color" in data
    assert nav.stack[-1].kind is FrameKind.HOME  # applied and closed


@requires_assets
def test_long_press_rgb_light_opens_grid(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    room = Room("hall", "Hall", entities=[_rgb_light("on")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)

    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.LIGHT_GRID
