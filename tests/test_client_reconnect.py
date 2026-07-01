import pytest

from homeassistant_api.errors import RequestError

from homedeck.ha import client as client_mod
from homedeck.ha.client import HaClient


class FakeWS:
    """Stand-in for homeassistant_api.WebsocketClient for reconnect tests."""

    instances: list["FakeWS"] = []

    def __init__(self, url, token):
        self.url, self.token = url, token
        self.exited = False
        self.service_calls: list[tuple] = []
        self.fail_next: Exception | None = None
        FakeWS.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.exited = True
        return False

    def trigger_service(self, domain, service, entity_id=None, **data):
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        self.service_calls.append((domain, service, entity_id, data))

    def get_states(self):
        return []


@pytest.fixture
def fake_ws(monkeypatch):
    FakeWS.instances.clear()
    monkeypatch.setattr(client_mod, "WebsocketClient", FakeWS)
    return FakeWS


def test_call_service_reconnects_after_dead_socket(fake_ws):
    c = HaClient("ws://ha/api/websocket", "tok")
    c.connect()
    c.call_service("light", "toggle", "light.a")
    assert len(fake_ws.instances) == 1

    # The idle socket dies: the next trigger_service raises a connection error.
    c._cmd.fail_next = OSError("connection reset by peer")
    c.call_service("light", "toggle", "light.b")

    # A fresh connection was opened, the old one closed, and the call retried.
    assert len(fake_ws.instances) == 2
    assert fake_ws.instances[0].exited is True
    assert ("light", "toggle", "light.b", {}) in fake_ws.instances[1].service_calls


def test_request_error_is_not_retried(fake_ws):
    c = HaClient("ws://ha/api/websocket", "tok")
    c.connect()
    c._cmd.fail_next = RequestError("bad", url="ws://ha", message="unknown service")

    with pytest.raises(RequestError):
        c.call_service("light", "toggle", "light.a")

    # A rejected command is a live connection: no reconnect.
    assert len(fake_ws.instances) == 1


def test_second_failure_propagates(fake_ws):
    c = HaClient("ws://ha/api/websocket", "tok")
    c.connect()

    # Fail once (triggers reconnect), then make the reconnected socket fail too.
    c._cmd.fail_next = OSError("dead")

    original_open = c._open_locked

    def open_then_break():
        original_open()
        c._cmd.fail_next = OSError("still dead")

    c._open_locked = open_then_break

    with pytest.raises(OSError):
        c.call_service("light", "toggle", "light.a")
