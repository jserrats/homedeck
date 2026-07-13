import pytest
from PIL import Image

from homedeck.deck import controller as ctrl_mod
from homedeck.deck.controller import DeckController


class FakeDeck:
    def __init__(self):
        self.alive = True
        self.opened = False
        self.closed = False
        self.callback = None
        self.images = {}
        self.brightness = None

    def open(self):
        self.opened = True

    def reset(self):
        pass

    def set_brightness(self, b):
        if not self.alive:
            raise OSError("gone")
        self.brightness = b

    def key_image_format(self):
        return {"size": (96, 96)}

    def key_layout(self):
        return (4, 8)

    def key_count(self):
        return 32

    def deck_type(self):
        return "Stream Deck XL"

    def id(self):
        return f"path-{id(self)}"

    def set_key_callback(self, cb):
        self.callback = cb

    def set_key_image(self, key, native):
        if not self.alive:
            raise OSError("unplugged")
        self.images[key] = native

    def close(self):
        self.closed = True

    def is_open(self):
        return self.opened and not self.closed


@pytest.fixture
def deck_env(monkeypatch, tmp_path):
    present: list[FakeDeck] = []

    class FakeManager:
        def enumerate(self):
            return list(present)

    monkeypatch.setattr(ctrl_mod, "DeviceManager", FakeManager)
    # to_native_key_format needs a real deck; make it a passthrough for tests
    monkeypatch.setattr(ctrl_mod.PILHelper, "to_native_key_format", lambda deck, image: image)
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))  # don't touch ~
    return present


def test_watchdog_detects_disconnect_and_reconnect(deck_env):
    d1 = FakeDeck()
    deck_env.append(d1)
    ctl = DeckController(brightness=50)
    assert ctl.deck is d1

    reconnects = []
    ctl.set_reconnect_callback(lambda: reconnects.append(True))
    ctl.set_callback(lambda key, pressed: None)

    # unplug: device no longer enumerated
    deck_env.clear()
    ctl._watchdog_once()
    assert ctl.deck is None and d1.closed is True

    # still gone -> stays disconnected, no crash
    ctl._watchdog_once()
    assert ctl.deck is None
    assert reconnects == []

    # replug: a fresh deck appears
    d2 = FakeDeck()
    deck_env.append(d2)
    ctl._watchdog_once()
    assert ctl.deck is d2
    assert d2.opened is True
    assert d2.callback is not None      # press callback re-registered
    assert reconnects == [True]         # view redrawn


def test_geometry_survives_disconnect(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController()
    # cached geometry keeps working while unplugged
    ctl._teardown_locked()
    assert ctl.deck is None
    assert ctl.key_count == 32
    assert ctl.key_size == (96, 96)
    assert ctl.cols == 8


def test_set_image_is_noop_while_disconnected(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController()
    ctl._teardown_locked()
    ctl.set_image(0, object())  # must not raise


def test_set_image_failure_triggers_teardown(deck_env):
    d1 = FakeDeck()
    deck_env.append(d1)
    ctl = DeckController()
    d1.alive = False  # writes now fail
    ctl.set_image(0, object())
    assert ctl.deck is None  # a failed write tore the dead handle down


def test_no_deck_raises(deck_env):
    with pytest.raises(RuntimeError):
        DeckController()  # deck_env is empty


# -- display rotation ---------------------------------------------------------

def test_rotation_changes_logical_columns(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController()
    assert (ctl.rotation, ctl.cols, ctl.key_count) == (0, 8, 32)
    ctl.set_rotation(90)
    assert (ctl.rotation, ctl.cols, ctl.key_count) == (90, 4, 32)   # portrait
    ctl.set_rotation(270)
    assert ctl.cols == 4
    ctl.set_rotation(180)
    assert ctl.cols == 8
    ctl.set_rotation(360)  # normalized back to 0
    assert (ctl.rotation, ctl.cols) == (0, 8)


def test_rotation_key_maps_are_bijections(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController()
    for deg in (0, 90, 180, 270):
        ctl.set_rotation(deg)
        n = ctl.key_count
        assert sorted(ctl._log_to_phys[k] for k in range(n)) == list(range(n))  # covers all keys
        for k in range(n):
            assert ctl._phys_to_log[ctl._log_to_phys[k]] == k  # inverse consistent
    ctl.set_rotation(0)
    assert all(ctl._log_to_phys[k] == k for k in range(ctl.key_count))  # identity at 0


def test_rotation_reading_order_not_reversed(deck_env):
    # At 90° the logical top-left key must land on the physical top-right key,
    # matching the clockwise image rotation (regression: it used to be reversed,
    # mapping to the physical bottom-left, so portrait read bottom-up/right-left).
    deck_env.append(FakeDeck())
    ctl = DeckController()
    ctl.set_rotation(90)
    assert ctl._log_to_phys[0] == 7  # physical top-right of a 4x8 deck


def test_set_image_writes_to_mapped_physical_key(deck_env):
    d = FakeDeck()
    deck_env.append(d)
    ctl = DeckController()
    ctl.set_rotation(90)
    ctl.set_image(0, Image.new("RGB", (96, 96), (1, 2, 3)))
    assert ctl._log_to_phys[0] in d.images  # logical 0 landed on its physical key


def test_press_maps_physical_key_to_logical(deck_env):
    d = FakeDeck()
    deck_env.append(d)
    ctl = DeckController()
    got = []
    ctl.set_callback(lambda key, pressed: got.append((key, pressed)))
    ctl.set_rotation(90)
    d.callback(d, 0, True)  # the deck fires physical key 0
    assert got == [(ctl._phys_to_log[0], True)]


def test_rotation_triggers_redraw_and_persists(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController()
    redraws = []
    ctl.set_reconnect_callback(lambda: redraws.append(True))
    ctl.cycle_rotation()
    assert ctl.rotation == 90
    assert redraws == [True]
    # persisted so a fresh controller starts rotated
    from homedeck import state
    assert state.load().get("rotation") == 90


def test_initial_rotation_applied(deck_env):
    deck_env.append(FakeDeck())
    ctl = DeckController(rotation_degrees=90)
    assert ctl.rotation == 90 and ctl.cols == 4


# -- display on/off (occupancy) ----------------------------------------------

def test_display_off_dims_and_on_restores_brightness(deck_env):
    d = FakeDeck()
    deck_env.append(d)
    ctl = DeckController(brightness=55)
    assert d.brightness == 55  # opened at the configured brightness

    ctl.set_display_on(False)
    assert d.brightness == 0            # backlight off
    ctl.set_display_on(True)
    assert d.brightness == 55           # configured brightness restored


def test_display_toggle_is_idempotent(deck_env):
    d = FakeDeck()
    deck_env.append(d)
    ctl = DeckController(brightness=40)
    ctl.set_display_on(True)            # already on -> no change
    assert d.brightness == 40
    ctl.set_display_on(False)
    ctl.set_display_on(False)           # still off, no error
    assert d.brightness == 0


def test_display_stays_off_across_reconnect(deck_env):
    d1 = FakeDeck()
    deck_env.append(d1)
    ctl = DeckController(brightness=70)
    ctl.set_display_on(False)

    # unplug and replug: the new deck must come back with the backlight off
    deck_env.clear()
    ctl._watchdog_once()
    d2 = FakeDeck()
    deck_env.append(d2)
    ctl._watchdog_once()
    assert ctl.deck is d2
    assert d2.brightness == 0


def test_set_brightness_while_off_stays_dark(deck_env):
    d = FakeDeck()
    deck_env.append(d)
    ctl = DeckController(brightness=60)
    ctl.set_display_on(False)
    ctl.set_brightness(80)              # reconfigure while off
    assert d.brightness == 0            # display stays dark
    ctl.set_display_on(True)
    assert d.brightness == 80           # new configured brightness applies
