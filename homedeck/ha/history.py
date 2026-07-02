"""State-history model, built from Home Assistant logbook events.

The logbook (``logbook/get_events``) gives each state change together with its
*context* — what caused it (an automation, a user, another entity) — which is
exactly the "timeline + what triggered it" we want.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, tzinfo


@dataclass
class HistoryEvent:
    when: float       # epoch seconds (UTC)
    state: str        # the new state, e.g. "on" / "off"
    trigger: str      # human-readable cause, may be "" if unknown
    time_label: str = ""  # absolute clock time, formatted in the target timezone
    rel_label: str = ""   # relative time, e.g. "5m ago"


def _trigger_label(item: dict) -> str:
    """Best-effort 'what triggered it' from a logbook entry's context fields."""
    name = item.get("context_name")
    if name:
        return f"by {name}"
    entity_name = item.get("context_entity_id_name")
    if entity_name:
        return f"by {entity_name}"
    if item.get("context_user_id"):
        return "manual"
    return ""


def _clock_label(ts: float, tz: tzinfo | None, today) -> str:
    """Absolute clock time: 'HH:MM' for today, 'Mon DD HH:MM' otherwise.

    ``tz`` None means use the container's local time (set via the TZ env var).
    """
    dt = datetime.fromtimestamp(ts, tz)
    if dt.date() == today:
        return dt.strftime("%H:%M")
    return dt.strftime("%b %d %H:%M")


def _relative_label(seconds_ago: float) -> str:
    """Compact relative time: now / 5m ago / 2h ago / 3d ago."""
    d = max(0, seconds_ago)
    if d < 60:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def parse_logbook(raw: list[dict], tz: tzinfo | None = None) -> list[HistoryEvent]:
    """Turn logbook entries into HistoryEvents, newest first.

    Times are formatted as absolute clock times in ``tz`` (HA's timezone) or, if
    None, the container's local timezone. Only entries with a state are kept.
    """
    today = datetime.now(tz).date()
    now_ts = time.time()
    events: list[HistoryEvent] = []
    for item in raw or []:
        state = item.get("state")
        when = item.get("when")
        if state is None or when is None:
            continue
        try:
            ts = float(when)
        except (TypeError, ValueError):
            continue
        events.append(HistoryEvent(
            when=ts, state=str(state), trigger=_trigger_label(item),
            time_label=_clock_label(ts, tz, today),
            rel_label=_relative_label(now_ts - ts),
        ))
    events.sort(key=lambda e: e.when, reverse=True)  # newest first
    return events
