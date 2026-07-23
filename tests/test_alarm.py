import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.model import IN_SCOPE_DOMAINS, DeviceEntity, Room, Status
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

FULL = 1 | 2 | 4 | 32  # ARM_HOME | ARM_AWAY | ARM_NIGHT | ARM_VACATION


def _alarm(state="disarmed", features=FULL):
    return DeviceEntity("alarm_control_panel.home", "House Alarm", "alarm_control_panel", state,
                        attributes={"supported_features": features})


# -- model --------------------------------------------------------------------

def test_alarm_in_scope_and_controllable():
    assert "alarm_control_panel" in IN_SCOPE_DOMAINS
    a = _alarm()
    assert a.is_alarm and a.is_controllable
    assert a.is_toggleable is False
    assert a.has_long_press is True


@pytest.mark.parametrize("state,status", [
    ("disarmed", Status.OFF),
    ("armed_home", Status.SECURE),
    ("armed_away", Status.SECURE),
    ("armed_night", Status.SECURE),
    ("arming", Status.PENDING),
    ("pending", Status.PENDING),
    ("triggered", Status.OPEN),
    ("unavailable", Status.UNAVAILABLE),
])
def test_alarm_status(state, status):
    assert _alarm(state).status is status


def test_single_press_toggles_arm_disarm():
    # disarmed -> arm with the preferred mode (away)
    assert _alarm("disarmed").service_call() == ("alarm_control_panel", "alarm_arm_away", "alarm_control_panel.home", {})
    # armed -> disarm
    assert _alarm("armed_home").service_call() == ("alarm_control_panel", "alarm_disarm", "alarm_control_panel.home", {})
    assert _alarm("triggered").service_call() == ("alarm_control_panel", "alarm_disarm", "alarm_control_panel.home", {})
    # preferred arm falls back when 'away' isn't supported
    assert _alarm("disarmed", features=1).service_call()[1] == "alarm_arm_home"


def test_supported_arm_modes():
    a = _alarm(features=FULL)
    assert a.supports_arm_home and a.supports_arm_away and a.supports_arm_night and a.supports_arm_vacation
    basic = _alarm(features=2)  # away only
    assert basic.supports_arm_away and not (basic.supports_arm_home or basic.supports_arm_night)


@requires_assets
def test_alarm_icon_is_state_aware():
    assert icons.resolve_icon_name("alarm_control_panel", None, None, state="disarmed") == "shield-off-outline"
    assert icons.resolve_icon_name("alarm_control_panel", None, None, state="armed_away") == "shield-lock"
    assert icons.resolve_icon_name("alarm_control_panel", None, None, state="triggered") == "shield-alert"


# -- navigation ---------------------------------------------------------------

def _nav(alarm):
    room = Room("hall", "Hall", entities=[alarm])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room],
                     on_service=lambda c: None, on_logbook=lambda e: [])
    return nav, room


@requires_assets
def test_short_press_toggles_long_press_opens_alarm_view(monkeypatch):
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    calls = []
    a = _alarm("armed_away")
    nav, room = _nav(a)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    key = next(k for k, x in nav.key_map.items() if x.kind is ActionKind.ENTITY)

    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)                 # short -> disarm (was armed)
    assert calls == [("alarm_control_panel", "alarm_disarm", "alarm_control_panel.home", {})]
    assert nav.stack[-1].kind is FrameKind.ROOM

    clock["t"] += 1.0
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)                 # long -> alarm control view
    assert nav.stack[-1].kind is FrameKind.ALARM


def test_alarm_view_has_disarm_and_supported_modes():
    a = _alarm("armed_away", features=1 | 2 | 4)  # home, away, night (no vacation)
    nav, _ = _nav(a)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ALARM, entity=a)]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert view[1].kind is ActionKind.ALARM_STATUS
    services = [x.data["call"][1] for x in view.values() if x.kind is ActionKind.SERVICE_BUTTON]
    assert services == ["alarm_disarm", "alarm_arm_home", "alarm_arm_away", "alarm_arm_night"]
    # the current mode (away) is highlighted
    away = next(x for x in view.values() if x.kind is ActionKind.SERVICE_BUTTON and x.data["call"][1] == "alarm_arm_away")
    assert away.data["active"]
    assert any(x.kind is ActionKind.MENU_ITEM and x.data["target"] == "history" for x in view.values())


def test_pressing_arm_button_calls_service():
    calls = []
    a = _alarm("disarmed")
    nav, _ = _nav(a)
    nav.on_service = calls.append
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ALARM, entity=a)]
    nav.key_map = nav._build_key_map()
    home_key = next(k for k, x in nav.key_map.items()
                    if x.kind is ActionKind.SERVICE_BUTTON and x.data["call"][1] == "alarm_arm_home")
    nav.handle_press(home_key, True)
    assert calls == [("alarm_control_panel", "alarm_arm_home", "alarm_control_panel.home", {})]
    assert nav.stack[-1].kind is FrameKind.ALARM  # stays in the panel


def test_alarm_included_in_security_folder():
    a = _alarm("armed_home")
    lock = DeviceEntity("lock.front", "Front", "lock", "locked")
    room = Room("hall", "Hall", entities=[a, lock])
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None)
    groups = nav._collect_security_groups()
    # alarms are the first security group, ahead of locks
    assert groups[0][0].entity_id == "alarm_control_panel.home"
    assert any(e.entity_id == "lock.front" for g in groups for e in g)


@requires_assets
def test_alarm_view_renders():
    a = _alarm("triggered")
    nav, _ = _nav(a)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ALARM, entity=a)]
    nav.render()  # all tiles render without error
