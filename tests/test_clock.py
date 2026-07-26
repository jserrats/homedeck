from datetime import datetime

import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _nav():
    room = Room("living", "Living", entities=[DeviceEntity("light.l", "L", "light", "on")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    return nav


def _fixed_clock(monkeypatch, dt):
    monkeypatch.setattr(nav_mod, "datetime",
                        type("D", (), {"now": staticmethod(lambda tz=None: dt)}))


def _key_of(key_map, kind):
    return next(k for k, a in key_map.items() if a.kind is kind)


# -- placement / interactivity ------------------------------------------------

def test_clock_and_date_tiles_in_the_home_band():
    home = _nav()._build_key_map()
    clock = [k for k, a in home.items() if a.kind is ActionKind.CLOCK]
    date = [k for k, a in home.items() if a.kind is ActionKind.DATE]
    assert len(clock) == 1 and len(date) == 1
    assert clock[0] >= 24 and date[0] >= 24  # in the reserved bottom band (8x4 grid)


def test_clock_tile_is_not_interactive():
    calls = []
    nav = _nav()
    nav.on_service = calls.append
    nav.key_map = nav._build_key_map()
    key = _key_of(nav.key_map, ActionKind.CLOCK)
    nav.handle_press(key, True)
    nav.handle_press(key, False)
    assert calls == [] and len(nav.stack) == 1  # nothing fired, still on home


# -- live updates via tick() --------------------------------------------------

@requires_assets
def test_tick_redraws_clock_only_when_the_minute_changes(monkeypatch):
    nav = _nav()
    _fixed_clock(monkeypatch, datetime(2026, 7, 23, 14, 49))
    nav.render()  # home; clock/date drawn for 14:49
    ck = _key_of(nav.key_map, ActionKind.CLOCK)

    nav.display.images.clear()
    nav.tick()                                   # first tick seeds the "shown" text
    nav.display.images.clear()
    nav.tick()                                   # same minute -> no redraw
    assert ck not in nav.display.images

    _fixed_clock(monkeypatch, datetime(2026, 7, 23, 14, 50))
    nav.tick()                                   # minute changed -> clock redrawn
    assert ck in nav.display.images


@requires_assets
def test_tick_redraws_date_when_the_day_changes(monkeypatch):
    nav = _nav()
    _fixed_clock(monkeypatch, datetime(2026, 7, 23, 23, 59))
    nav.render()
    dk = _key_of(nav.key_map, ActionKind.DATE)
    nav.tick()  # seed
    nav.display.images.clear()

    _fixed_clock(monkeypatch, datetime(2026, 7, 24, 0, 0))
    nav.tick()  # new day
    assert dk in nav.display.images


@requires_assets
def test_tick_noop_off_home(monkeypatch):
    nav = _nav()
    _fixed_clock(monkeypatch, datetime(2026, 7, 23, 14, 49))
    from homedeck.ui.navigation import Frame, FrameKind
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.SETTINGS)]
    nav.key_map = nav._build_key_map()
    nav.display.images.clear()
    nav.tick()
    assert nav.display.images == {}  # not on home -> clock isn't ticked


# -- rendering ----------------------------------------------------------------

@requires_assets
def test_clock_and_date_render_reflects_the_time():
    r = KeyRenderer((96, 96))
    assert r.clock_tile(datetime(2026, 7, 23, 14, 49)).tobytes() != \
        r.clock_tile(datetime(2026, 7, 23, 14, 50)).tobytes()
    assert r.date_tile(datetime(2026, 7, 23, 12, 0)).tobytes() != \
        r.date_tile(datetime(2026, 7, 24, 12, 0)).tobytes()
