"""Calendar model, built from Home Assistant ``calendar.*`` entities.

Two data sources, deliberately:

  * a calendar entity's **attributes** (``message`` / ``start_time`` /
    ``end_time`` / ``all_day``) already describe its *current or next* event and
    arrive over the normal ``state_changed`` stream — that's all the home-screen
    tile needs, at no extra network cost, and
  * the ``calendar.get_events`` service, fetched on demand when the agenda view
    is opened, which gives every event in a window rather than just the next one.

Both are normalised into :class:`CalendarEvent`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, tzinfo

# How far ahead the agenda view looks.
AGENDA_DAYS = 7


@dataclass
class CalendarEvent:
    entity_id: str
    calendar: str      # friendly name of the owning calendar
    summary: str
    start: float       # epoch seconds
    end: float         # epoch seconds
    all_day: bool = False
    time_label: str = ""  # "18:00" today, "Tue 09:30" later, "All day"
    clock: str = ""       # time of day alone: "09:30" / "All day" (agenda columns)
    rel_label: str = ""   # "Now", "in 25m", "Tomorrow", ...

    def is_ongoing(self, now: float) -> bool:
        return self.start <= now < self.end


@dataclass
class CalendarDay:
    """A day's worth of events, for the one-column-per-day agenda."""

    label: str        # weekday, e.g. "Mon"
    date_label: str   # month/day, e.g. "Aug 03"
    events: list[CalendarEvent]


@dataclass
class Calendar:
    """One ``calendar.*`` entity and its current-or-next event."""

    entity_id: str
    name: str
    event: CalendarEvent | None = None

    def update(self, state: str, attributes: dict, tz: tzinfo | None = None) -> None:
        self.name = attributes.get("friendly_name") or self.name
        self.event = event_from_attributes(self.entity_id, self.name, state, attributes, tz)

    @classmethod
    def from_state(cls, entity_id: str, state: str, attributes: dict,
                   tz: tzinfo | None = None) -> "Calendar":
        name = attributes.get("friendly_name") or entity_id.split(".", 1)[-1].replace("_", " ").title()
        return cls(entity_id, name, event_from_attributes(entity_id, name, state, attributes, tz))


def _parse_dt(value, tz: tzinfo | None) -> float | None:
    """Epoch seconds from any of the date/time shapes HA uses for calendars.

    Accepts the attribute form (``"2026-08-03 18:00:00"``, naive and in HA's
    timezone), the ``get_events`` ISO form (``{"dateTime": "...+02:00"}`` or a
    bare ``{"date": "2026-08-03"}`` for all-day), and plain strings of either.
    Returns None when the value is missing or unparseable.
    """
    if isinstance(value, dict):  # get_events: {"dateTime": ...} | {"date": ...}
        value = value.get("dateTime") or value.get("date")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:  # HA sends local (its own timezone) wall-clock times
        dt = dt.replace(tzinfo=tz) if tz is not None else dt.astimezone()
    return dt.timestamp()


def _is_all_day(value) -> bool:
    """True when a start/end value carries a date but no time of day.

    ``calendar.get_events`` returns plain ISO strings (a bare ``"2026-08-05"``
    for an all-day event); the REST calendar endpoint nests them as
    ``{"date": ...}`` / ``{"dateTime": ...}``. Both shapes turn up in the wild.
    """
    if isinstance(value, dict):
        return "date" in value and not value.get("dateTime")
    if isinstance(value, str):
        text = value.strip()
        return len(text) == 10 and "T" not in text and " " not in text
    return isinstance(value, date) and not isinstance(value, datetime)


def _clock_label(ts: float, tz: tzinfo | None, now: datetime, all_day: bool) -> str:
    """Absolute label: 'All day' / 'HH:MM' today / 'Tue 09:30' another day."""
    dt = datetime.fromtimestamp(ts, tz)
    days = (dt.date() - now.date()).days
    if all_day:
        if days == 0:
            return "All day"
        if days == 1:
            return "Tomorrow"
        return dt.strftime("%a %b %d")
    if days == 0:
        return dt.strftime("%H:%M")
    if days < 7:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%b %d %H:%M")


def _relative_label(event: CalendarEvent, now: datetime, tz: tzinfo | None) -> str:
    """Compact relative label: Now / in 25m / in 3h / Tomorrow / in 3d.

    Anything on a later date is counted in days, so an event at 09:30 tomorrow
    reads "Tomorrow" rather than a less useful "in 21h".
    """
    now_ts = now.timestamp()
    if event.is_ongoing(now_ts):
        return "Now"
    delta = event.start - now_ts
    if delta < 0:
        return "Ended"
    days = (datetime.fromtimestamp(event.start, tz).date() - now.date()).days
    if days >= 2:
        return f"in {days}d"
    if days == 1:
        return "Tomorrow"
    if delta < 60:
        return "Now"
    if delta < 3600:
        return f"in {int(delta // 60)}m"
    return f"in {int(delta // 3600)}h"


def format_labels(event: CalendarEvent, now: datetime, tz: tzinfo | None = None) -> CalendarEvent:
    """Fill in ``time_label`` / ``rel_label`` relative to ``now``. Returns the event."""
    event.time_label = _clock_label(event.start, tz, now, event.all_day)
    event.clock = "All day" if event.all_day else datetime.fromtimestamp(event.start, tz).strftime("%H:%M")
    event.rel_label = _relative_label(event, now, tz)
    return event


def event_from_attributes(entity_id: str, name: str, state: str, attributes: dict,
                          tz: tzinfo | None = None) -> CalendarEvent | None:
    """The current-or-next event described by a calendar entity's attributes.

    Returns None when the calendar has no upcoming event (or is unavailable).
    """
    if (state or "").lower() in ("unavailable", "unknown"):
        return None
    summary = attributes.get("message") or attributes.get("summary")
    start = _parse_dt(attributes.get("start_time"), tz)
    if not summary or start is None:
        return None
    all_day = bool(attributes.get("all_day"))
    end = _parse_dt(attributes.get("end_time"), tz)
    if end is None:
        end = start + 86400 if all_day else start + 3600
    return CalendarEvent(entity_id, name, str(summary), start, end, all_day)


def next_event(calendars: list[Calendar], now: datetime,
               tz: tzinfo | None = None) -> CalendarEvent | None:
    """The soonest event across ``calendars``: an ongoing one wins, else the
    earliest upcoming start. Already-ended events are ignored."""
    now_ts = now.timestamp()
    candidates = [c.event for c in calendars if c.event is not None and c.event.end > now_ts]
    if not candidates:
        return None
    # Ongoing events sort first, then by start time.
    best = min(candidates, key=lambda e: (0 if e.is_ongoing(now_ts) else 1, e.start))
    return format_labels(best, now, tz)


def parse_events(raw_by_entity: dict[str, list[dict]], names: dict[str, str],
                 now: datetime, tz: tzinfo | None = None) -> list[CalendarEvent]:
    """Turn a ``calendar.get_events`` response into a time-sorted event list.

    ``names`` maps entity_id -> friendly calendar name. Events that have already
    ended are dropped; ongoing events sort first.
    """
    now_ts = now.timestamp()
    events: list[CalendarEvent] = []
    for entity_id, raw in (raw_by_entity or {}).items():
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            summary = item.get("summary") or item.get("message")
            start = _parse_dt(item.get("start"), tz)
            if not summary or start is None:
                continue
            all_day = _is_all_day(item.get("start"))
            end = _parse_dt(item.get("end"), tz)
            if end is None:
                end = start + 86400 if all_day else start + 3600
            if end <= now_ts:
                continue  # already over
            events.append(format_labels(
                CalendarEvent(entity_id, names.get(entity_id, entity_id), str(summary),
                              start, end, all_day),
                now, tz,
            ))
    events.sort(key=lambda e: (0 if e.is_ongoing(now_ts) else 1, e.start))
    return events


def group_by_day(events: list[CalendarEvent], now: datetime, tz: tzinfo | None = None,
                 span: int | None = None) -> list[CalendarDay]:
    """Group a time-sorted event list into days, earliest day first.

    An event already under way is filed under *today* even if it started on an
    earlier date, so a multi-day event doesn't open a column in the past.

    ``span`` emits that many consecutive days starting today whether or not they
    have events, so the agenda keeps one column per day and stays easy to scan.
    Events falling outside the span still get their own day appended, so nothing
    is ever dropped.
    """
    now_ts = now.timestamp()
    by_date: dict[date, CalendarDay] = {}
    for offset in range(span or 0):
        day_date = now.date() + timedelta(days=offset)
        by_date[day_date] = CalendarDay(day_date.strftime("%a"), day_date.strftime("%b %d"), [])
    for event in events:
        moment = datetime.fromtimestamp(max(event.start, now_ts), tz)
        day = by_date.get(moment.date())
        if day is None:
            day = CalendarDay(moment.strftime("%a"), moment.strftime("%b %d"), [])
            by_date[moment.date()] = day
        day.events.append(event)
    return [by_date[key] for key in sorted(by_date)]


def events_from_calendars(calendars: list[Calendar], now: datetime,
                          tz: tzinfo | None = None) -> list[CalendarEvent]:
    """Fallback agenda when ``calendar.get_events`` is unavailable: each
    calendar's current-or-next event, time-sorted."""
    now_ts = now.timestamp()
    events = [c.event for c in calendars if c.event is not None and c.event.end > now_ts]
    events.sort(key=lambda e: (0 if e.is_ongoing(now_ts) else 1, e.start))
    return [format_labels(e, now, tz) for e in events]
