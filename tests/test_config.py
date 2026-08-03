import pytest

from homedeck.config import Config
from homedeck.ha.calendar import AGENDA_DAYS


@pytest.fixture(autouse=True)
def base_env(monkeypatch):
    """The required vars, so a local .env can't change what these tests see."""
    monkeypatch.setenv("HA_URL", "ws://ha.local:8123/api/websocket")
    monkeypatch.setenv("HA_TOKEN", "token")
    for name in ("HOMEDECK_BRIGHTNESS", "HOMEDECK_WEATHER_ENTITY", "HOMEDECK_OCCUPANCY_ENTITY",
                 "HOMEDECK_ROTATION", "HOMEDECK_AGENDA_DAYS"):
        monkeypatch.delenv(name, raising=False)


def test_requires_url_and_token(monkeypatch):
    monkeypatch.setenv("HA_TOKEN", "")
    with pytest.raises(ValueError, match="HA_TOKEN"):
        Config.from_env()


def test_url_must_be_a_websocket_url(monkeypatch):
    monkeypatch.setenv("HA_URL", "http://ha.local:8123")
    with pytest.raises(ValueError, match="ws://"):
        Config.from_env()


def test_agenda_days_defaults_to_two_weeks():
    assert Config.from_env().agenda_days == AGENDA_DAYS == 14


def test_agenda_days_is_configurable(monkeypatch):
    monkeypatch.setenv("HOMEDECK_AGENDA_DAYS", "30")
    assert Config.from_env().agenda_days == 30


@pytest.mark.parametrize("raw,expected", [
    ("0", 1),        # clamped up: a zero-day agenda would show nothing
    ("999", 60),     # clamped down
    ("not-a-number", AGENDA_DAYS),
    ("", AGENDA_DAYS),
])
def test_agenda_days_rejects_nonsense(monkeypatch, raw, expected):
    monkeypatch.setenv("HOMEDECK_AGENDA_DAYS", raw)
    assert Config.from_env().agenda_days == expected


def test_rotation_normalizes_to_quarter_turns(monkeypatch):
    monkeypatch.setenv("HOMEDECK_ROTATION", "100")
    assert Config.from_env().rotation == 90
