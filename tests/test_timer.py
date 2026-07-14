import time

import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import DeviceEntity, Room, Status, format_duration
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")


def _timer(state, **attrs):
    return DeviceEntity("timer.pomodoro", "Pomodoro", "timer", state, attributes=attrs)


# -- model --------------------------------------------------------------------

def test_timer_status_by_state():
    assert _timer("active").status is Status.ON
    assert _timer("paused").status is Status.PENDING
    assert _timer("idle").status is Status.OFF
    assert _timer("unavailable").status is Status.UNAVAILABLE


def test_timer_display_value():
    assert format_duration(272) == "4:32"
    assert format_duration(3660) == "1:01:00"
    # paused -> remaining attribute
    assert _timer("paused", remaining="0:04:32").display_value() == "4:32"
    # idle -> configured duration
    assert _timer("idle", duration="0:10:00").display_value() == "10:00"


def test_timer_active_uses_finishes_at():
    finishes = time.time() + 90
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(finishes, timezone.utc).isoformat()
    secs = _timer("active", finishes_at=iso, remaining="0:05:00").remaining_seconds()
    assert 85 <= secs <= 90  # ~90s from finishes_at, not the 5:00 remaining attr


def test_timer_press_pauses_or_starts():
    assert _timer("active").service_call() == ("timer", "pause", "timer.pomodoro", {})
    assert _timer("paused").service_call() == ("timer", "start", "timer.pomodoro", {})
    assert _timer("idle").service_call() == ("timer", "start", "timer.pomodoro", {})


def test_timer_is_controllable_and_long_press():
    t = _timer("active")
    assert t.is_controllable is True
    assert t.is_timer is True
    assert t.has_long_press is True


# -- navigation ---------------------------------------------------------------

def _nav(state="active"):
    room = Room("kitchen", "Kitchen", entities=[_timer(state, remaining="0:04:00", duration="0:05:00")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    return nav


@requires_assets
def test_short_press_toggles_long_press_opens_detail(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    nav = _nav("active")
    nav.on_service = calls.append
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.ENTITY)

    # short press pauses (active)
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)
    assert calls == [("timer", "pause", "timer.pomodoro", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    # long press opens the options menu; "Controls" opens the timer detail view
    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.ENTITY_MENU
    ctrl = next(k for k, a in nav.key_map.items()
                if a.kind is ActionKind.MENU_ITEM and a.data["target"] == "timer")
    nav.handle_press(ctrl, True)
    assert nav.stack[-1].kind is FrameKind.TIMER


def test_timer_detail_layout_and_actions():
    nav = _nav("active")
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.TIMER, entity=_timer("active"))]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.TIMER_STATUS
    # active -> primary action is Pause; plus Cancel and Finish
    services = [a.data["service"] for k, a in view.items() if a.kind is ActionKind.TIMER_ACTION]
    assert services == ["pause", "cancel", "finish"]


def test_timer_detail_primary_is_resume_when_paused():
    nav = _nav("paused")
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.TIMER, entity=_timer("paused"))]
    view = nav._build_key_map()
    assert view[2].data == {"service": "start", "label": "Resume", "icon": "play",
                            "color": nav_mod.renderer_mod.SECURE}


def test_pressing_timer_action_calls_service():
    calls = []
    nav = _nav("active")
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.TIMER, entity=_timer("active"))]
    nav.key_map = nav._build_key_map()
    nav.handle_press(4, True)  # Finish
    assert calls == [("timer", "finish", "timer.pomodoro", {})]
    assert nav.stack[-1].kind is FrameKind.TIMER  # stays in the detail view


# -- live countdown ticker ----------------------------------------------------

@requires_assets
def test_tick_rerenders_active_timer_only():
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(time.time() + 300, timezone.utc).isoformat()
    room = Room("kitchen", "Kitchen", entities=[
        _timer("active", finishes_at=iso, remaining="0:05:00"),
        DeviceEntity("light.hood", "Hood", "light", "on"),
    ])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.render()
    timer_key = next(k for k, a in nav.key_map.items()
                     if a.kind is ActionKind.ENTITY and a.entity.is_timer)
    light_key = next(k for k, a in nav.key_map.items()
                     if a.kind is ActionKind.ENTITY and a.entity.domain == "light")

    display.images.clear()
    nav.tick()
    assert timer_key in display.images       # active timer re-rendered
    assert light_key not in display.images   # non-timer untouched


@requires_assets
def test_tick_ignores_paused_and_idle_timers():
    room = Room("kitchen", "Kitchen", entities=[_timer("paused", remaining="0:02:00")])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.render()
    display.images.clear()
    nav.tick()
    assert display.images == {}  # paused timer doesn't tick


@requires_assets
def test_tick_updates_timer_detail_status():
    from datetime import datetime, timezone
    iso = datetime.fromtimestamp(time.time() + 300, timezone.utc).isoformat()
    active = _timer("active", finishes_at=iso, remaining="0:05:00")
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.TIMER, entity=active)]
    nav.render()
    display.images.clear()
    nav.tick()
    assert 1 in display.images  # the TIMER_STATUS tile (key 1) refreshed


def test_tick_noop_when_disconnected():
    nav = _nav("active")
    nav._disconnected = True
    nav.display.images.clear()
    nav.tick()
    assert nav.display.images == {}
