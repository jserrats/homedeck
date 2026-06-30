import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

COLS, ROWS, TOTAL = 8, 4, 32
requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _ct_light(state="on"):
    return DeviceEntity(
        "light.lamp", "Lamp", "light", state,
        attributes={
            "supported_color_modes": ["color_temp"],
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
        },
    )


def _plain_light():
    return DeviceEntity("light.plain", "Plain", "light", "on",
                        attributes={"supported_color_modes": ["onoff"]})


# -- model --------------------------------------------------------------------

def test_supports_light_grid_detection():
    assert _ct_light().supports_light_grid is True
    assert _plain_light().supports_light_grid is False
    assert _ct_light().has_long_press is True
    assert _plain_light().has_long_press is False
    assert _ct_light().long_press_call() is None  # grid is not a plain service call


def test_light_grid_levels_4x8():
    bri, kelvins = _ct_light().light_grid_levels(ROWS, COLS)
    assert bri == [10, 40, 70, 100]
    assert len(kelvins) == 8
    assert kelvins[0] == 2000 and kelvins[-1] == 6500  # the light's full range
    assert kelvins == sorted(kelvins)


# -- grid layout (whole deck, 4x8) --------------------------------------------

def _grid_nav():
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_ct_light())]
    return nav


def test_grid_fills_the_whole_deck():
    key_map = _grid_nav()._build_key_map()
    cells = [k for k, a in key_map.items() if a.kind is ActionKind.GRID_CELL]
    assert sorted(cells) == list(range(TOTAL))  # all 32 keys are presets


def test_grid_axes_brightness_rows_temp_cols():
    key_map = _grid_nav()._build_key_map()
    # top row brightest (100), bottom row dimmest (10)
    top = {key_map[c].data["brightness_pct"] for c in range(0, COLS)}
    bottom = {key_map[(ROWS - 1) * COLS + c].data["brightness_pct"] for c in range(COLS)}
    assert top == {100} and bottom == {10}
    # color temperature increases left→right along a row, spanning full range
    row0 = [key_map[c].data["color_temp_kelvin"] for c in range(COLS)]
    assert row0 == sorted(row0)
    assert row0[0] == 2000 and row0[-1] == 6500


# -- behavior -----------------------------------------------------------------

@requires_assets
def test_long_press_opens_grid_short_press_toggles(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    room = Room("hall", "Hall", entities=[_ct_light("on")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=calls.append)
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
    nav.handle_press(key, False)            # long -> open grid
    assert nav.stack[-1].kind is FrameKind.LIGHT_GRID


@requires_assets
def test_pressing_a_cell_applies_and_closes():
    calls = []
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=calls.append)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_ct_light())]
    nav.key_map = nav._build_key_map()

    data = nav.key_map[10].data  # some interior cell
    nav.handle_press(10, True)

    assert calls == [("light", "turn_on", "light.lamp", data)]
    assert "brightness_pct" in data and "color_temp_kelvin" in data
    assert nav.stack[-1].kind is FrameKind.HOME  # popped back
