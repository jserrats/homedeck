import time
from datetime import datetime, timezone

import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.graph import (
    DEFAULT_RANGE,
    TIMEFRAMES,
    build_series,
    samples_from_history,
    samples_from_statistics,
    timeframe,
)
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

NOW = 1_700_000_000.0


def _sensor(state="21.4", **attrs):
    return DeviceEntity("sensor.t", "Temp", "sensor", state,
                        attributes={"unit_of_measurement": "°C", **attrs})


# -- model --------------------------------------------------------------------

def test_numeric_sensors_are_plottable():
    assert _sensor("21.4").is_numeric_sensor is True
    assert _sensor("0").is_numeric_sensor is True
    assert DeviceEntity("sensor.w", "W", "sensor", "running").is_numeric_sensor is False
    assert DeviceEntity("binary_sensor.m", "M", "binary_sensor", "on").is_numeric_sensor is False
    assert DeviceEntity("climate.l", "L", "climate", "heat").is_numeric_sensor is False


def test_timestamp_sensors_are_not_plottable():
    # reads as "in 2h", not a value -- nothing to chart
    ts = DeviceEntity("sensor.bus", "Bus", "sensor", "2026-07-23T14:49:00+00:00",
                      device_class="timestamp")
    assert ts.is_numeric_sensor is False


def test_unavailable_sensor_with_a_unit_keeps_its_graph():
    # a momentary dropout shouldn't make the Graph option disappear
    assert _sensor("unavailable").is_numeric_sensor is True
    assert DeviceEntity("sensor.x", "X", "sensor", "unavailable").is_numeric_sensor is False


# -- parsing ------------------------------------------------------------------

def test_history_accepts_compressed_and_long_keys():
    raw = [
        {"s": "21.4", "lu": NOW - 60},                       # websocket compressed form
        {"state": "21.6", "last_updated": NOW - 30},         # long form
        {"state": "21.8", "last_changed": NOW - 10},         # last_changed fallback
    ]
    assert samples_from_history(raw) == [(NOW - 60, 21.4), (NOW - 30, 21.6), (NOW - 10, 21.8)]


def test_history_drops_non_numeric_rows_and_sorts():
    raw = [
        {"s": "21.6", "lu": NOW - 30},
        {"s": "unavailable", "lu": NOW - 20},  # a gap, not a zero
        {"s": "unknown", "lu": NOW - 15},
        {"s": "21.4", "lu": NOW - 60},         # out of order
        {"s": "21.8"},                         # no timestamp
    ]
    assert samples_from_history(raw) == [(NOW - 60, 21.4), (NOW - 30, 21.6)]


def test_history_parses_iso_timestamps():
    raw = [{"state": "5", "last_updated": "2023-11-14T22:13:20+00:00"}]
    (when, value), = samples_from_history(raw)
    assert value == 5
    assert datetime.fromtimestamp(when, timezone.utc).year == 2023


def test_statistics_converts_millisecond_starts():
    raw = [{"start": NOW * 1000, "mean": 21.5, "min": 20.0, "max": 23.0}]
    assert samples_from_statistics(raw) == [(NOW, 21.5)]


def test_statistics_falls_back_to_max_then_min():
    raw = [
        {"start": NOW * 1000, "max": 23.0},
        {"start": (NOW + 3600) * 1000, "min": 19.0},
        {"start": (NOW + 7200) * 1000},  # nothing usable -> dropped
        {"mean": 21.0},                  # no timestamp -> dropped
    ]
    assert samples_from_statistics(raw) == [(NOW, 23.0), (NOW + 3600, 19.0)]


# -- the series ---------------------------------------------------------------

def _series(samples, label="1h"):
    return build_series(samples, timeframe(label), "°C", now=NOW)


def test_build_series_clips_to_the_window():
    samples = [(NOW - 7200, 10.0), (NOW - 1800, 20.0), (NOW - 60, 30.0)]
    series = _series(samples)  # 1h window drops the two-hour-old reading
    assert [v for _, v in series.samples] == [20.0, 30.0]
    assert series.latest == 30.0
    assert (series.minimum, series.maximum) == (20.0, 30.0)


def test_build_series_is_none_without_readings():
    assert _series([]) is None
    assert _series([(NOW - 7200, 10.0)]) is None  # all outside the window


def test_flat_series_gets_a_padded_plot_range():
    series = _series([(NOW - 1800, 5.0), (NOW - 60, 5.0)])
    assert (series.minimum, series.maximum) == (5.0, 5.0)  # the readings are reported as-is
    low, high = series.bounds()                            # only the plot pads them
    assert low < 5.0 < high


def test_bounds_are_the_readings_extremes():
    series = _series([(NOW - 1800, 10.0), (NOW - 60, 20.0)])
    assert series.bounds() == (10.0, 20.0)


def test_average_is_time_weighted():
    # 5 held for most of the hour, 25 for the last minute: a plain mean would
    # say 15, but the sensor read ~5 nearly the whole time
    series = _series([(NOW - 3600, 5.0), (NOW - 60, 25.0)])
    assert series.average == pytest.approx(5.0 + 20 * (60 / 3600))
    assert series.average < 6.0


def test_average_of_a_single_reading_is_that_reading():
    assert _series([(NOW - 60, 7.5)]).average == 7.5


def test_stat_labels_report_min_avg_max_with_units():
    series = _series([(NOW - 3600, 10.0), (NOW - 1800, 20.0)])
    captions = [caption for caption, _ in series.stat_labels()]
    values = dict(series.stat_labels())
    assert captions == ["Min", "Avg", "Max"]
    assert values["Min"] == "10 °C" and values["Max"] == "20 °C"
    # the 20 held for the second half of the window, so the mean sits between
    assert values["Avg"] == "15 °C"


def test_stats_ignore_readings_outside_the_window():
    samples = [(NOW - 7200, 99.0), (NOW - 1800, 10.0), (NOW - 60, 20.0)]
    series = _series(samples)  # the 1h window excludes the 99
    assert (series.minimum, series.maximum) == (10.0, 20.0)
    assert series.average < 20.0


def test_columns_bucket_and_average():
    # two readings in the first half, one in the second
    samples = [(NOW - 3600, 10.0), (NOW - 3000, 20.0), (NOW - 600, 40.0)]
    cols = _series(samples).columns(2)
    assert cols == [15.0, 40.0]


def test_columns_interpolate_interior_gaps_only():
    # readings at the very start and the very end, nothing between
    samples = [(NOW - 3600, 0.0), (NOW - 1, 40.0)]
    cols = _series(samples).columns(5)
    assert cols[0] == 0.0 and cols[-1] == 40.0
    assert all(c is not None for c in cols)          # the middle is bridged
    assert cols[2] == pytest.approx(20.0)


def test_columns_leave_leading_and_trailing_gaps_empty():
    # everything sits in the middle of the window; the edges stay unknown
    samples = [(NOW - 1900, 10.0), (NOW - 1700, 12.0)]
    cols = _series(samples).columns(4)
    assert cols[0] is None and cols[-1] is None
    assert any(c is not None for c in cols)


def test_only_the_top_axis_label_carries_the_unit():
    low, mid, high = _series([(NOW - 1800, 10.0), (NOW - 60, 20.0)]).value_labels()
    assert (low, mid, high) == ("10", "15", "20 °C")


def test_time_labels_switch_to_weekdays_on_long_windows():
    samples = [(NOW - 600, 1.0)]
    hours = [label for _, label in _series(samples, "1h").time_labels(timezone.utc)]
    week = [label for _, label in _series([(NOW - 600, 1.0)], "1w").time_labels(timezone.utc)]
    assert all(":" in label for label in hours)       # 1h -> clock times
    assert all(":" not in label for label in week)    # 1w -> weekday names


def test_timeframes_cover_the_documented_windows():
    assert [tf.label for tf in TIMEFRAMES] == ["1h", "4h", "12h", "24h", "1w"]
    assert [tf.hours for tf in TIMEFRAMES] == [1, 4, 12, 24, 168]
    # short windows read raw history; long ones prefer statistics
    assert [tf.period for tf in TIMEFRAMES] == [None, None, "5minute", "5minute", "hour"]
    assert timeframe("nonsense").label == DEFAULT_RANGE


# -- navigation ---------------------------------------------------------------

def _nav(entity=None, stats=None, history=None, calls=None):
    """A Navigation over an 8x4 export display, recording every series fetch."""
    entity = entity or _sensor()
    room = Room("hall", "Hall", entities=[entity])
    calls = calls if calls is not None else []

    def on_statistics(eid, hours, period):
        calls.append(("statistics", eid, hours, period))
        return list(stats or [])

    def on_history(eid, hours):
        calls.append(("history", eid, hours))
        return list(history or [])

    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None,
                     on_statistics=on_statistics, on_history=on_history)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.ROOM, room=room)]
    nav.key_map = nav._build_key_map()
    return nav, entity, calls


def _rows(count=8, span=3600):
    return [{"s": str(20 + i), "lu": time.time() - span + i * (span / count)} for i in range(count)]


def test_graph_menu_item_opens_the_chart(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, _ = _nav(history=_rows())
    nav._dispatch_menu_target(entity, "graph")
    assert nav.stack[-1].kind is FrameKind.GRAPH
    assert nav.stack[-1].data["range"] == DEFAULT_RANGE
    assert nav.stack[-1].series is not None


def test_short_windows_skip_statistics():
    nav, entity, calls = _nav(history=_rows())
    nav._fetch_series(entity, timeframe("1h"))
    assert calls == [("history", "sensor.t", 1)]


def test_long_windows_prefer_statistics():
    stats = [{"start": (time.time() - 3600 + i * 600) * 1000, "mean": 20 + i} for i in range(6)]
    nav, entity, calls = _nav(stats=stats, history=_rows())
    series = nav._fetch_series(entity, timeframe("24h"))
    assert calls == [("statistics", "sensor.t", 24, "5minute")]  # history never consulted
    assert series is not None


def test_falls_back_to_history_without_statistics():
    # a sensor with no state_class records no long-term statistics
    nav, entity, calls = _nav(stats=[], history=_rows())
    series = nav._fetch_series(entity, timeframe("1w"))
    assert calls == [("statistics", "sensor.t", 168, "hour"), ("history", "sensor.t", 168)]
    assert series is not None


def test_a_failing_fetch_leaves_an_empty_chart():
    def boom(*_args):
        raise RuntimeError("recorder is down")

    nav, entity, _ = _nav()
    nav.on_statistics = boom
    nav.on_history = boom
    assert nav._fetch_series(entity, timeframe("24h")) is None  # logged, not raised


@requires_assets
def test_graph_layout_is_a_control_band_over_a_mosaic(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, _ = _nav(history=_rows())
    nav._dispatch_menu_target(entity, "graph")
    view = nav._build_key_map()

    assert view[0].kind is ActionKind.BACK
    ranges = [a for a in view.values() if a.kind is ActionKind.GRAPH_RANGE]
    assert [a.data["range"] for a in ranges] == ["1h", "4h", "12h", "24h", "1w"]
    assert [a.data["range"] for a in ranges if a.data["active"]] == [DEFAULT_RANGE]
    assert view[6].data["target"] == "history"  # the logbook stays one press away

    cells = {k: a for k, a in view.items() if a.kind is ActionKind.GRAPH_CELL}
    assert set(cells) == set(range(8, 32))  # rows 1-3 of the 8x4 deck
    assert {a.data["cell"] for a in cells.values()} == {(r, c) for r in range(3) for c in range(8)}
    # one panel is drawn per render and shared by every cell
    assert len({id(a.data["panel"]) for a in cells.values()}) == 1


@requires_assets
def test_switching_the_range_refetches_in_place(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, calls = _nav(history=_rows())
    nav._dispatch_menu_target(entity, "graph")
    depth = len(nav.stack)
    calls.clear()

    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.GRAPH_RANGE and a.data["range"] == "1w")
    nav.handle_press(key, True)

    assert calls == [("statistics", "sensor.t", 168, "hour"), ("history", "sensor.t", 168)]
    assert nav.stack[-1].data["range"] == "1w"
    assert len(nav.stack) == depth  # Back still leaves the chart, not the previous window
    assert [a.data["range"] for a in nav.key_map.values()
            if a.kind is ActionKind.GRAPH_RANGE and a.data["active"]] == ["1w"]


@requires_assets
def test_the_chosen_range_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, _ = _nav(history=_rows())
    nav._dispatch_menu_target(entity, "graph")
    nav._set_graph_range("4h")

    nav2, entity2, _ = _nav(history=_rows())
    nav2._dispatch_menu_target(entity2, "graph")
    assert nav2.stack[-1].data["range"] == "4h"


@requires_assets
def test_panel_header_wraps_on_a_narrow_deck():
    # 8 columns fit name + Now + Min/Avg/Max + the window on one line; 4 don't,
    # so the stats drop to a second row and the plot starts lower.
    renderer = KeyRenderer((96, 96))
    series = _series([(NOW - 3000, 10.0), (NOW - 60, 20.0)])
    wide = renderer.graph_panel(series, 8, 3, title="Temp", range_label="1h")
    narrow = renderer.graph_panel(series, 4, 6, title="Temp", range_label="1h")
    assert wide.size == (768, 288) and narrow.size == (384, 576)


@requires_assets
def test_graph_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, _ = _nav(history=_rows(count=40))
    nav._dispatch_menu_target(entity, "graph")
    nav.render()
    assert len(nav.display.images) == 32


@requires_assets
def test_graph_without_data_renders_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, _ = _nav()  # no statistics, no history
    nav._dispatch_menu_target(entity, "graph")
    assert nav.stack[-1].series is None
    nav.render()  # falls back to a flat list with a "No data" tile
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    assert any(a.kind is ActionKind.GRAPH_CELL and a.data.get("panel") is None
               for a in view.values())


@requires_assets
def test_graph_press_targets_are_inert(tmp_path, monkeypatch):
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))
    nav, entity, calls = _nav(history=_rows())
    nav._dispatch_menu_target(entity, "graph")
    calls.clear()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.GRAPH_CELL)
    nav.handle_press(key, True)
    nav.handle_press(key, False)
    assert calls == [] and nav.stack[-1].kind is FrameKind.GRAPH
