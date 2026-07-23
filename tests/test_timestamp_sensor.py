from datetime import datetime, timedelta, timezone

import pytest

from homedeck.deck import icons
from homedeck.ha.model import DeviceEntity, humanize_datetime

NOW = 1_000_000.0


def _sensor(state, device_class=None):
    attrs = {"device_class": device_class} if device_class else {}
    return DeviceEntity("sensor.x", "X", "sensor", state, attributes=attrs, device_class=device_class)


# -- humanize_datetime --------------------------------------------------------

@pytest.mark.parametrize("offset,expected", [
    (0, "now"),
    (-30, "now"),
    (-300, "5m ago"),
    (-7200, "2h ago"),
    (-3 * 86400, "3d ago"),
    (300, "in 5m"),
    (7200, "in 2h"),
    (2 * 86400, "in 2d"),
])
def test_humanize_relative_past_and_future(offset, expected):
    dt = datetime.fromtimestamp(NOW + offset, timezone.utc)
    assert humanize_datetime(dt, now=NOW) == expected


# -- sensor display detection -------------------------------------------------

def test_timestamp_device_class_is_humanized():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    assert _sensor(ts, "timestamp").display_value() == "5m ago"


def test_plain_sensor_with_iso_datetime_is_humanized():
    # no device_class, but the value is a full ISO datetime (contains 'T')
    ts = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    assert _sensor(ts).display_value() == "in 3h"


def test_the_example_value_is_parsed():
    # a fixed example resolves to a relative string, not the raw ISO text
    out = _sensor("2026-07-23T14:49:00+00:00").display_value()
    assert out != "2026-07-23T14:49:00+00:00"
    assert out == "now" or out.endswith("ago") or out.startswith("in ")


def test_numbers_and_text_are_unaffected():
    assert _sensor("78.4", None).attributes == {}
    assert _sensor("78.40000001").display_value() == "78.4"
    assert _sensor("hello").display_value() == "hello"


def test_plain_date_string_without_class_is_left_raw():
    # a date-only value with no timestamp/date device_class is not a datetime sensor
    assert _sensor("2026-07-23").display_value() == "2026-07-23"


def test_date_device_class_is_humanized():
    d = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    out = _sensor(d, "date").display_value()
    assert out.endswith("ago")


def test_unavailable_timestamp_sensor_shows_dash():
    assert _sensor("unavailable", "timestamp").display_value() == "—"


@pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")
def test_timestamp_sensor_icon_is_clock():
    assert icons.resolve_icon_name("sensor", "timestamp", None) == "clock-outline"
    assert icons.resolve_icon_name("sensor", "date", None) == "calendar"
