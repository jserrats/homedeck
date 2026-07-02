import pytest

from homedeck.deck import controller as ctrl_mod
from homedeck.deck.controller import DeckController


class FakeDeck:
    def __init__(self):
        self.alive = True
        self.opened = False
        self.closed = False
        self.callback = None
        self.images = {}

    def open(self):
        self.opened = True

    def reset(self):
        pass

    def set_brightness(self, b):
        if not self.alive:
            raise OSError("gone")

    def key_image_format(self):
        return {"size": (96, 96)}

    def key_layout(self):
        return (4, 8)

    def key_count(self):
        return 32

    def deck_type(self):
        return "Stream Deck XL"

    def get_firmware_version(self):
        if not self.alive:
            raise OSError("unplugged")
        return "1.0"

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
def deck_env(monkeypatch):
    present: list[FakeDeck] = []

    class FakeManager:
        def enumerate(self):
            return list(present)

    monkeypatch.setattr(ctrl_mod, "DeviceManager", FakeManager)
    # to_native_key_format needs a real deck; make it a passthrough for tests
    monkeypatch.setattr(ctrl_mod.PILHelper, "to_native_key_format", lambda deck, image: image)
    return present


def test_watchdog_detects_disconnect_and_reconnect(deck_env):
    d1 = FakeDeck()
    deck_env.append(d1)
    ctl = DeckController(brightness=50)
    assert ctl.deck is d1

    reconnects = []
    ctl.set_reconnect_callback(lambda: reconnects.append(True))
    ctl.set_callback(lambda key, pressed: None)

    # unplug: device gone + probe fails
    d1.alive = False
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
