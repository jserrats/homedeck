import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, Status
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation


def _lock(state):
    return DeviceEntity("lock.front", "Front Door", "lock", state)


# -- model --------------------------------------------------------------------

def test_lock_is_controllable():
    assert _lock("locked").is_controllable is True


@pytest.mark.parametrize("state,status", [
    ("locked", Status.SECURE),
    ("unlocked", Status.OFF),
    ("locking", Status.PENDING),
    ("unlocking", Status.PENDING),
    ("opening", Status.PENDING),
    ("jammed", Status.UNAVAILABLE),
    ("unavailable", Status.UNAVAILABLE),
])
def test_lock_status(state, status):
    assert _lock(state).status is status


def test_lock_short_press_toggles_by_state():
    assert _lock("locked").service_call() == ("lock", "unlock", "lock.front")
    assert _lock("unlocked").service_call() == ("lock", "lock", "lock.front")
    assert _lock("jammed").service_call() == ("lock", "lock", "lock.front")


def test_lock_long_press_opens():
    lock = _lock("locked")
    assert lock.has_long_press is True
    assert lock.long_press_call() == ("lock", "open", "lock.front")


def test_non_lock_has_no_long_press():
    light = DeviceEntity("light.x", "X", "light", "on")
    assert light.has_long_press is False
    assert light.long_press_call() is None


# -- icons --------------------------------------------------------------------

@pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")
def test_lock_icon_is_state_aware():
    assert icons.resolve_icon_name("lock", None, None, state="locked") == "lock"
    assert icons.resolve_icon_name("lock", None, None, state="unlocked") == "lock-open-variant"
    assert icons.resolve_icon_name("lock", None, None, state="jammed") == "lock-alert"
    assert icons.resolve_icon_name("lock", None, None, state="locking") == "lock-clock"
    assert icons.resolve_icon_name("lock", None, None, state="unlocking") == "lock-clock"
    # an explicit HA icon still wins
    assert icons.resolve_icon_name("lock", None, "mdi:door", state="locked") == "door"


# -- navigation long press ----------------------------------------------------

def _nav_with_lock(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])

    calls: list[tuple] = []
    room = Room("hall", "Hall", entities=[
        _lock("locked"),
        DeviceEntity("light.hall", "Hall Light", "light", "off"),
    ])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=calls.append)
    # Go to the room view without rendering (avoids needing font assets).
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    return nav, calls, clock


def _key_of(nav, entity_id):
    return next(
        k for k, a in nav.key_map.items()
        if a.kind is ActionKind.ENTITY and a.entity and a.entity.entity_id == entity_id
    )


def test_short_press_locks_or_unlocks(monkeypatch):
    nav, calls, clock = _nav_with_lock(monkeypatch)
    key = _key_of(nav, "lock.front")

    nav.handle_press(key, pressed=True)   # lock defers on press-down
    assert calls == []
    clock["t"] += 0.1                       # quick release
    nav.handle_press(key, pressed=False)
    assert calls == [("lock", "unlock", "lock.front")]  # was locked -> unlock


def test_long_press_opens_door(monkeypatch):
    nav, calls, clock = _nav_with_lock(monkeypatch)
    key = _key_of(nav, "lock.front")

    nav.handle_press(key, pressed=True)
    clock["t"] += 1.0                       # held past the long-press threshold
    nav.handle_press(key, pressed=False)
    assert calls == [("lock", "open", "lock.front")]


def test_light_fires_immediately_on_press_down(monkeypatch):
    nav, calls, clock = _nav_with_lock(monkeypatch)
    key = _key_of(nav, "light.hall")

    nav.handle_press(key, pressed=True)     # non-lock: immediate
    assert calls == [("light", "toggle", "light.hall")]
    nav.handle_press(key, pressed=False)    # release is a no-op
    assert len(calls) == 1
