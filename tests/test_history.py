from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.history import parse_logbook
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

# newest last here; parse_logbook sorts newest-first
LOGBOOK = [
    {"when": 1000.0, "state": "off", "context_name": "Sunset"},
    {"when": 2000.0, "state": "on", "context_entity_id_name": "Hall Motion"},
    {"when": 3000.0, "state": "off", "context_user_id": "abc123"},
    {"when": 4000.0, "state": "on"},  # unknown trigger
    {"when": 5000.0},  # no state -> dropped
]


# -- model --------------------------------------------------------------------

def test_switch_and_binary_sensor_support_history():
    assert DeviceEntity("switch.x", "X", "switch", "on").supports_history is True
    assert DeviceEntity("binary_sensor.m", "M", "binary_sensor", "on").supports_history is True
    assert DeviceEntity("light.l", "L", "light", "on").supports_history is False
    # both gain a long press
    assert DeviceEntity("switch.x", "X", "switch", "on").has_long_press is True
    assert DeviceEntity("binary_sensor.m", "M", "binary_sensor", "on").has_long_press is True


def test_parse_logbook_orders_and_extracts_triggers():
    events = parse_logbook(LOGBOOK, timezone.utc)
    assert [e.state for e in events] == ["on", "off", "on", "off"]  # newest first, no-state dropped
    triggers = {e.when: e.trigger for e in events}
    assert triggers[1000.0] == "by Sunset"
    assert triggers[2000.0] == "by Hall Motion"
    assert triggers[3000.0] == "manual"
    assert triggers[4000.0] == ""


def test_time_label_is_absolute_and_timezone_aware():
    # 1970-01-01 00:33:20 UTC -> 00:33 in UTC, 01:33 in Madrid (CET, +1)
    utc = {e.when: e.time_label for e in parse_logbook(LOGBOOK, timezone.utc)}
    madrid = {e.when: e.time_label for e in parse_logbook(LOGBOOK, ZoneInfo("Europe/Madrid"))}
    assert utc[2000.0] == "Jan 01 00:33"      # old date -> includes the date
    assert madrid[2000.0] == "Jan 01 01:33"   # Madrid is UTC+1 in winter


def test_relative_labels_alongside_absolute():
    import time as _t
    now = _t.time()
    raw = [
        {"when": now, "state": "on"},
        {"when": now - 300, "state": "off"},
        {"when": now - 7200, "state": "on"},
        {"when": now - 172800, "state": "off"},
    ]
    rels = {e.when: e.rel_label for e in parse_logbook(raw, timezone.utc)}
    assert rels[now] == "now"
    assert rels[now - 300] == "5m ago"
    assert rels[now - 7200] == "2h ago"
    assert rels[now - 172800] == "2d ago"
    # absolute label is still populated too
    assert all(e.time_label for e in parse_logbook(raw, timezone.utc))


def test_time_label_shows_only_time_for_today():
    now = datetime.now(timezone.utc)
    ts = now.timestamp()
    label = parse_logbook([{"when": ts, "state": "on"}], timezone.utc)[0].time_label
    assert label == now.strftime("%H:%M")  # today -> HH:MM only, no date


# -- navigation ---------------------------------------------------------------

def _nav(logbook=LOGBOOK):
    room = Room("hall", "Hall", entities=[
        DeviceEntity("switch.fan", "Fan", "switch", "on"),
        DeviceEntity("binary_sensor.motion", "Motion", "binary_sensor", "off",
                     attributes={"device_class": "motion"}, device_class="motion"),
    ])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None,
                     on_logbook=lambda eid: logbook)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    return nav, room


@requires_assets
def test_long_press_switch_opens_history(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    nav, _ = _nav()
    nav.on_service = calls.append
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.entity_id == "switch.fan")

    # short press toggles
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)
    assert calls == [("switch", "toggle", "switch.fan", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    # long press opens history
    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.HISTORY
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.HISTORY_TITLE
    events = [a for a in view.values() if a.kind is ActionKind.HISTORY_EVENT]
    assert len(events) == 4  # the no-state entry is dropped


@requires_assets
def test_long_press_binary_sensor_opens_history(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    nav, _ = _nav()
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.ENTITY and a.entity.entity_id == "binary_sensor.motion")

    # short press does nothing (not controllable, stays put)
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.ROOM

    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)
    assert nav.stack[-1].kind is FrameKind.HISTORY
