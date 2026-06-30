import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

COLS, TOTAL = 8, 32
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
    assert _ct_light().has_long_press is True   # grid lights defer to release
    assert _plain_light().has_long_press is False
    assert _ct_light().long_press_call() is None  # grid is not a plain service call


def test_light_grid_levels():
    bri, kelvins = _ct_light().light_grid_levels()
    assert bri == [10, 40, 70, 100]
    assert kelvins[0] == 2000 and kelvins[-1] == 6500
    assert kelvins == sorted(kelvins) and len(kelvins) == 4


# -- grid layout --------------------------------------------------------------

def _grid_nav(held_key):
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_ct_light(), held_key=held_key)]
    return nav


def test_grid_avoids_held_key_left_half_uses_right():
    nav = _grid_nav(held_key=9)  # row 1, col 1 (left half)
    key_map = nav._build_key_map()
    cells = [k for k, a in key_map.items() if a.kind is ActionKind.GRID_CELL]
    assert len(cells) == 16
    # all cells in the right half (cols 4..7), never on the held key
    assert all((k % COLS) >= 4 for k in cells)
    assert 9 not in cells
    assert key_map[9].kind is ActionKind.GRID_SOURCE


def test_grid_avoids_held_key_right_half_uses_left():
    nav = _grid_nav(held_key=6)  # row 0, col 6 (right half)
    key_map = nav._build_key_map()
    cells = [k for k, a in key_map.items() if a.kind is ActionKind.GRID_CELL]
    assert all((k % COLS) < 4 for k in cells)
    assert key_map[6].kind is ActionKind.GRID_SOURCE


def test_grid_axes_brightness_rows_temp_cols():
    nav = _grid_nav(held_key=0)  # held top-left -> grid in right half
    key_map = nav._build_key_map()
    cells = {k: a for k, a in key_map.items() if a.kind is ActionKind.GRID_CELL}
    # top row brightest (100), bottom row dimmest (10)
    top = [a.data["brightness_pct"] for k, a in cells.items() if k // COLS == 0]
    bottom = [a.data["brightness_pct"] for k, a in cells.items() if k // COLS == 3]
    assert set(top) == {100} and set(bottom) == {10}
    # within a row, color temp increases left to right
    row0 = sorted((k % COLS, a.data["color_temp_kelvin"]) for k, a in cells.items() if k // COLS == 0)
    kelvins = [k for _, k in row0]
    assert kelvins == sorted(kelvins)


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

    # short press -> toggle
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)
    assert calls == [("light", "toggle", "light.lamp", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    # long press -> opens the grid
    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.LIGHT_GRID


@requires_assets
def test_pressing_a_cell_applies_and_closes(monkeypatch):
    calls = []
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=calls.append)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_ct_light(), held_key=0)]
    nav.key_map = nav._build_key_map()

    cell_key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.GRID_CELL)
    data = nav.key_map[cell_key].data
    nav.handle_press(cell_key, True)

    assert calls == [("light", "turn_on", "light.lamp", data)]
    assert "brightness_pct" in data and "color_temp_kelvin" in data
    assert nav.stack[-1].kind is FrameKind.HOME  # popped back


@requires_assets
def test_source_key_closes_grid():
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.LIGHT_GRID, entity=_ct_light(), held_key=3)]
    nav.key_map = nav._build_key_map()
    nav.handle_press(3, True)  # the GRID_SOURCE key
    assert nav.stack[-1].kind is FrameKind.HOME
