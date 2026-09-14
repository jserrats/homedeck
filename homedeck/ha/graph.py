"""Numeric series for the sensor graph view.

Home Assistant offers a numeric sensor's past two ways, and neither suits every
window:

  * ``history/history_during_period`` returns every recorded state — exact, but a
    week of a chatty sensor is tens of thousands of rows, and
  * ``recorder/statistics_during_period`` returns 5-minute or hourly aggregates —
    small and smooth, but only for sensors that keep long-term statistics (those
    with a ``state_class``).

So the short windows read raw history and the long ones prefer statistics, with
history as the fallback. Everything here is pure: the fetching lives on the
client, the drawing on the renderer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, tzinfo

from .model import _format_number, _parse_iso_datetime, _with_unit

# Anything past this is milliseconds, not seconds (year 5138 in epoch seconds).
_MILLIS_THRESHOLD = 1e11


@dataclass(frozen=True)
class Timeframe:
    label: str          # shown on the button, e.g. "4h"
    hours: int
    period: str | None  # None -> raw history; "5minute"/"hour" -> statistics first


TIMEFRAMES = [
    Timeframe("1h", 1, None),
    Timeframe("4h", 4, None),
    Timeframe("12h", 12, "5minute"),
    Timeframe("24h", 24, "5minute"),
    Timeframe("1w", 168, "hour"),
]

DEFAULT_RANGE = "24h"


def timeframe(label: str | None) -> Timeframe:
    """The Timeframe named ``label``, falling back to the default window."""
    for tf in TIMEFRAMES:
        if tf.label == label:
            return tf
    return next(tf for tf in TIMEFRAMES if tf.label == DEFAULT_RANGE)


# -- parsing ------------------------------------------------------------------

def _epoch(raw: object) -> float | None:
    """Seconds since the epoch from a number (seconds or millis) or ISO string."""
    if isinstance(raw, str):
        dt = _parse_iso_datetime(raw)
        return dt.timestamp() if dt is not None else None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return value / 1000.0 if value > _MILLIS_THRESHOLD else value


def _number(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def samples_from_statistics(raw: list[dict]) -> list[tuple[float, float]]:
    """(epoch, value) pairs from ``recorder/statistics_during_period`` rows.

    The mean is the representative value; ``min``/``max`` stand in for periods
    that only recorded those. Timestamps arrive in milliseconds.
    """
    samples: list[tuple[float, float]] = []
    for row in raw or []:
        when = _epoch(row.get("start"))
        if when is None:
            continue
        for key in ("mean", "max", "min", "state"):
            value = _number(row.get(key))
            if value is not None:
                samples.append((when, value))
                break
    samples.sort(key=lambda s: s[0])
    return samples


def samples_from_history(raw: list[dict]) -> list[tuple[float, float]]:
    """(epoch, value) pairs from ``history/history_during_period`` rows.

    The websocket API sends compressed keys (``s``/``lu``); the long form is
    accepted too. Rows whose state isn't a number (``unavailable``, ``unknown``)
    are dropped — they're gaps in the series, not zeros.
    """
    samples: list[tuple[float, float]] = []
    for row in raw or []:
        value = _number(row.get("s", row.get("state")))
        if value is None:
            continue
        when = _epoch(row.get("lu", row.get("last_updated", row.get("last_changed"))))
        if when is None:
            continue
        samples.append((when, value))
    samples.sort(key=lambda s: s[0])
    return samples


# -- the series ---------------------------------------------------------------

@dataclass
class Series:
    """A sensor's readings over one window, ready to plot."""

    samples: list[tuple[float, float]]  # (epoch, value), oldest first
    start: float                        # window start, epoch seconds
    end: float                          # window end (now)
    unit: str
    minimum: float   # the true extremes of the readings, not the plotted range
    maximum: float
    average: float   # time-weighted, so a long-held value outweighs a blip
    latest: float

    def columns(self, n: int) -> list[float | None]:
        """Average the samples into ``n`` equal time buckets across the window.

        Empty buckets stay None and are filled in only *between* two known
        columns; a leading or trailing gap stays None so the line never invents
        data it doesn't have.
        """
        if n <= 0:
            return []
        span = self.end - self.start
        totals = [0.0] * n
        counts = [0] * n
        for when, value in self.samples:
            slot = 0 if span <= 0 else int((when - self.start) / span * n)
            slot = max(0, min(n - 1, slot))
            totals[slot] += value
            counts[slot] += 1
        cols: list[float | None] = [
            totals[i] / counts[i] if counts[i] else None for i in range(n)
        ]
        return _interpolate(cols)

    def bounds(self) -> tuple[float, float]:
        """The low/high the plot is drawn against.

        Normally the readings' own extremes; a perfectly flat series gets padded
        so it draws through the middle instead of dividing by a zero-height range.
        """
        if self.maximum > self.minimum:
            return self.minimum, self.maximum
        pad = abs(self.minimum) * 0.05 or 0.5
        return self.minimum - pad, self.maximum + pad

    def value_labels(self) -> tuple[str, str, str]:
        """Axis labels for the low, middle and high of the plotted range.

        Only the top one carries the unit — the usual chart convention, and the
        left gutter is only one key wide.
        """
        low, high = self.bounds()
        return (_format_number(low), _format_number((low + high) / 2),
                _with_unit(_format_number(high), self.unit))

    def stat_labels(self) -> list[tuple[str, str]]:
        """(caption, value) for the window's summary readings."""
        return [(caption, _with_unit(_format_number(value), self.unit))
                for caption, value in (("Min", self.minimum), ("Avg", self.average),
                                       ("Max", self.maximum))]

    def latest_label(self) -> str:
        return _with_unit(_format_number(self.latest), self.unit)

    def time_labels(self, tz: tzinfo | None = None) -> list[tuple[float, str]]:
        """Three x-axis ticks as (position 0..1, clock label).

        Windows up to a day are labelled by time, longer ones by weekday, which
        is what tells you where you are on a week-long chart.
        """
        span = self.end - self.start
        fmt = "%H:%M" if span <= 36 * 3600 else "%a"
        return [
            (pos, datetime.fromtimestamp(self.start + span * pos, tz).strftime(fmt))
            for pos in (0.0, 0.5, 1.0)
        ]


def _interpolate(cols: list[float | None]) -> list[float | None]:
    """Linearly fill None runs that sit between two known values."""
    known = [i for i, v in enumerate(cols) if v is not None]
    if len(known) < 2:
        return cols
    filled = list(cols)
    for left, right in zip(known, known[1:]):
        if right - left < 2:
            continue
        lo, hi = cols[left], cols[right]
        for i in range(left + 1, right):
            filled[i] = lo + (hi - lo) * (i - left) / (right - left)  # type: ignore[operator]
    return filled


def _time_weighted_mean(samples: list[tuple[float, float]], end: float) -> float:
    """Average each reading over the time it was in force, not per sample.

    Raw history records a row only when the value *changes*, so a plain mean
    would count a ten-second blip as heavily as a value that held all afternoon.
    Statistics rows are evenly spaced, so weighting leaves them unchanged.
    """
    if len(samples) == 1:
        return samples[0][1]
    total = span = 0.0
    for (when, value), (next_when, _) in zip(samples, samples[1:]):
        held = next_when - when
        total += value * held
        span += held
    tail = end - samples[-1][0]  # the last reading holds until the window closes
    if tail > 0:
        total += samples[-1][1] * tail
        span += tail
    return total / span if span > 0 else samples[-1][1]


def build_series(samples: list[tuple[float, float]], tf: Timeframe, unit: str = "",
                 now: float | None = None) -> Series | None:
    """Clip ``samples`` to ``tf``'s window and describe them. None when empty.

    A perfectly flat series gets its range padded, so it draws as a line through
    the middle of the panel instead of dividing by a zero-height range.
    """
    end = time.time() if now is None else now
    start = end - tf.hours * 3600
    inside = sorted((s for s in samples if start <= s[0] <= end), key=lambda s: s[0])
    if not inside:
        return None
    values = [v for _, v in inside]
    return Series(samples=inside, start=start, end=end, unit=unit,
                  minimum=min(values), maximum=max(values),
                  average=_time_weighted_mean(inside, end), latest=values[-1])
