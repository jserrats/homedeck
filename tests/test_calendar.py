from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.calendar import (
    AGENDA_DAYS,
    Calendar,
    CalendarDay,
    CalendarEvent,
    event_from_attributes,
    events_from_calendars,
    group_by_day,
    next_event,
    parse_events,
)
from homedeck.ha.model import DeviceEntity, Room
from homedeck.ui import navigation as nav_mod
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation, layout_calendar

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

TZ = ZoneInfo("UTC")
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=TZ)  # a Monday


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Keep the calendar selection out of the developer's real state file."""
    monkeypatch.setenv("HOMEDECK_STATE_FILE", str(tmp_path / "state.json"))


def _attrs(message, start, end, all_day=False, name=None):
    attrs = {"message": message, "start_time": start, "end_time": end, "all_day": all_day}
    if name:
        attrs["friendly_name"] = name
    return attrs


def _calendar(entity_id, name, message=None, start=None, end=None, all_day=False):
    if message is None:
        return Calendar(entity_id, name, None)
    return Calendar.from_state(entity_id, "on", _attrs(message, start, end, all_day, name), TZ)


# -- model --------------------------------------------------------------------


def test_event_from_attributes_parses_has_naive_local_times():
    event = event_from_attributes(
        "calendar.personal", "Personal", "on",
        _attrs("Dentist", "2026-08-03 18:00:00", "2026-08-03 19:00:00"), TZ,
    )
    assert event is not None
    assert event.summary == "Dentist"
    assert event.calendar == "Personal"
    assert event.start == datetime(2026, 8, 3, 18, 0, tzinfo=TZ).timestamp()
    assert event.end == datetime(2026, 8, 3, 19, 0, tzinfo=TZ).timestamp()
    assert event.all_day is False


def test_event_from_attributes_none_without_an_event():
    assert event_from_attributes("calendar.x", "X", "off", {}, TZ) is None
    assert event_from_attributes("calendar.x", "X", "unavailable",
                                 _attrs("Nope", "2026-08-03 18:00:00", None), TZ) is None


def test_event_from_attributes_defaults_a_missing_end():
    event = event_from_attributes("calendar.x", "X", "on",
                                  _attrs("Party", "2026-08-03 18:00:00", None), TZ)
    assert event.end == event.start + 3600
    all_day = event_from_attributes("calendar.x", "X", "on",
                                    _attrs("Holiday", "2026-08-04", None, all_day=True), TZ)
    assert all_day.end == all_day.start + 86400


def test_next_event_prefers_an_ongoing_event():
    ongoing = _calendar("calendar.a", "A", "Standup", "2026-08-03 11:30:00", "2026-08-03 12:30:00")
    soon = _calendar("calendar.b", "B", "Dentist", "2026-08-03 13:00:00", "2026-08-03 14:00:00")
    assert next_event([soon, ongoing], NOW, TZ).summary == "Standup"


def test_next_event_picks_the_earliest_upcoming_and_skips_ended():
    ended = _calendar("calendar.a", "A", "Breakfast", "2026-08-03 08:00:00", "2026-08-03 09:00:00")
    later = _calendar("calendar.b", "B", "Dinner", "2026-08-03 20:00:00", "2026-08-03 21:00:00")
    sooner = _calendar("calendar.c", "C", "Dentist", "2026-08-03 13:00:00", "2026-08-03 14:00:00")
    assert next_event([ended, later, sooner], NOW, TZ).summary == "Dentist"


def test_next_event_none_when_nothing_upcoming():
    assert next_event([], NOW, TZ) is None
    assert next_event([_calendar("calendar.a", "A")], NOW, TZ) is None


def test_labels_for_today_tomorrow_and_later():
    today = next_event([_calendar("calendar.a", "A", "Dentist",
                                  "2026-08-03 18:00:00", "2026-08-03 19:00:00")], NOW, TZ)
    assert today.time_label == "18:00"
    assert today.rel_label == "in 6h"

    tomorrow = next_event([_calendar("calendar.a", "A", "Standup",
                                     "2026-08-04 09:30:00", "2026-08-04 10:00:00")], NOW, TZ)
    assert tomorrow.time_label == "Tue 09:30"
    assert tomorrow.rel_label == "Tomorrow"

    ongoing = next_event([_calendar("calendar.a", "A", "Focus",
                                    "2026-08-03 11:00:00", "2026-08-03 13:00:00")], NOW, TZ)
    assert ongoing.rel_label == "Now"


def test_all_day_events_are_labelled_all_day():
    event = next_event([_calendar("calendar.a", "A", "Holiday",
                                  "2026-08-03", "2026-08-04", all_day=True)], NOW, TZ)
    assert event.time_label == "All day"


def test_parse_events_sorts_across_calendars_and_drops_past_ones():
    # calendar.get_events returns plain ISO strings for start/end.
    raw = {
        "calendar.work": [
            {"summary": "Standup", "start": "2026-08-04T09:30:00+00:00",
             "end": "2026-08-04T10:00:00+00:00"},
            {"summary": "Retro", "start": "2026-08-03T08:00:00+00:00",
             "end": "2026-08-03T09:00:00+00:00"},  # already over
        ],
        "calendar.personal": [
            {"summary": "Dentist", "start": "2026-08-03T13:00:00+00:00",
             "end": "2026-08-03T14:00:00+00:00"},
        ],
    }
    events = parse_events(raw, {"calendar.work": "Work", "calendar.personal": "Personal"}, NOW, TZ)
    assert [e.summary for e in events] == ["Dentist", "Standup"]
    assert events[0].calendar == "Personal"


@pytest.mark.parametrize("start,end", [
    ("2026-08-05", "2026-08-06"),                          # get_events service form
    ({"date": "2026-08-05"}, {"date": "2026-08-06"}),      # REST calendar endpoint form
])
def test_parse_events_recognises_all_day_events(start, end):
    raw = {"calendar.h": [{"summary": "Holiday", "start": start, "end": end}]}
    events = parse_events(raw, {"calendar.h": "Holidays"}, NOW, TZ)
    assert len(events) == 1
    assert events[0].all_day is True
    assert events[0].time_label == "Wed Aug 05"


def test_parse_events_handles_the_nested_datetime_form():
    raw = {"calendar.w": [{"summary": "Standup", "start": {"dateTime": "2026-08-04T09:30:00+00:00"},
                           "end": {"dateTime": "2026-08-04T10:00:00+00:00"}}]}
    events = parse_events(raw, {"calendar.w": "Work"}, NOW, TZ)
    assert [(e.summary, e.all_day, e.time_label) for e in events] == [("Standup", False, "Tue 09:30")]


def test_group_by_day_splits_into_days_earliest_first():
    raw = {"calendar.w": [
        {"summary": "Standup", "start": "2026-08-04T09:30:00+00:00", "end": "2026-08-04T10:00:00+00:00"},
        {"summary": "Dentist", "start": "2026-08-03T13:00:00+00:00", "end": "2026-08-03T14:00:00+00:00"},
        {"summary": "Retro", "start": "2026-08-04T15:00:00+00:00", "end": "2026-08-04T16:00:00+00:00"},
    ]}
    days = group_by_day(parse_events(raw, {"calendar.w": "Work"}, NOW, TZ), NOW, TZ)
    assert [(d.label, d.date_label, [e.summary for e in d.events]) for d in days] == [
        ("Mon", "Aug 03", ["Dentist"]),
        ("Tue", "Aug 04", ["Standup", "Retro"]),
    ]


def test_group_by_day_span_emits_every_day_even_the_empty_ones():
    raw = {"calendar.w": [
        {"summary": "Standup", "start": "2026-08-05T09:30:00+00:00", "end": "2026-08-05T10:00:00+00:00"},
    ]}
    days = group_by_day(parse_events(raw, {"calendar.w": "Work"}, NOW, TZ), NOW, TZ, span=4)
    assert [(d.date_label, [e.summary for e in d.events]) for d in days] == [
        ("Aug 03", []),            # today, nothing on
        ("Aug 04", []),
        ("Aug 05", ["Standup"]),
        ("Aug 06", []),
    ]


def test_group_by_day_span_still_keeps_events_beyond_the_window():
    raw = {"calendar.w": [
        {"summary": "Far off", "start": "2026-08-20T09:00:00+00:00", "end": "2026-08-20T10:00:00+00:00"},
    ]}
    days = group_by_day(parse_events(raw, {"calendar.w": "Work"}, NOW, TZ), NOW, TZ, span=3)
    assert [d.date_label for d in days] == ["Aug 03", "Aug 04", "Aug 05", "Aug 20"]


def test_group_by_day_files_an_ongoing_event_under_today():
    """A multi-day event that started earlier must not open a column in the past."""
    raw = {"calendar.h": [{"summary": "Conference", "start": "2026-08-01T09:00:00+00:00",
                           "end": "2026-08-05T18:00:00+00:00"}]}
    days = group_by_day(parse_events(raw, {"calendar.h": "Holidays"}, NOW, TZ), NOW, TZ)
    assert [(d.label, d.date_label) for d in days] == [("Mon", "Aug 03")]


def test_events_carry_a_clock_label_without_the_day():
    event = next_event([_calendar("calendar.a", "A", "Standup",
                                  "2026-08-04 09:30:00", "2026-08-04 10:00:00")], NOW, TZ)
    assert event.time_label == "Tue 09:30"  # home tile: needs the day
    assert event.clock == "09:30"           # agenda column: the header says the day


def test_events_from_calendars_is_the_agenda_fallback():
    cals = [
        _calendar("calendar.a", "A", "Dinner", "2026-08-03 20:00:00", "2026-08-03 21:00:00"),
        _calendar("calendar.b", "B", "Dentist", "2026-08-03 13:00:00", "2026-08-03 14:00:00"),
        _calendar("calendar.c", "C"),
    ]
    events = events_from_calendars(cals, NOW, TZ)
    assert [e.summary for e in events] == ["Dentist", "Dinner"]


# -- navigation ---------------------------------------------------------------


def _nav(calendars=None, on_calendar_events=None, monkeypatch=None, cols=8, agenda_days=None):
    room = Room("living", "Living", entities=[DeviceEntity("light.l", "L", "light", "on")])
    display = ExportDisplay(cols=cols) if cols != 8 else ExportDisplay()
    extra = {} if agenda_days is None else {"agenda_days": agenda_days}
    nav = Navigation(display, KeyRenderer(display.key_size), [room], on_service=lambda c: None,
                     calendars=calendars or [], on_calendar_events=on_calendar_events, tz=TZ,
                     **extra)
    if monkeypatch is not None:
        _freeze(nav, monkeypatch)
    nav.key_map = nav._build_key_map()
    return nav


def _freeze(nav, monkeypatch, now=NOW):
    """Pin datetime.now() inside navigation so tile text is deterministic."""
    monkeypatch.setattr(nav_mod, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: now)}))


def _two_calendars():
    return [
        _calendar("calendar.personal", "Personal", "Dentist",
                  "2026-08-03 13:00:00", "2026-08-03 14:00:00"),
        _calendar("calendar.work", "Work", "Standup",
                  "2026-08-04 09:30:00", "2026-08-04 10:00:00"),
    ]


def _calendar_key(nav):
    return next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_CALENDAR)


def test_calendar_tile_sits_in_the_home_band(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    key = _calendar_key(nav)
    assert key >= 24  # the reserved bottom row of an 8x4 deck
    settings_key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_SETTINGS)
    assert key < settings_key  # Settings stays pinned last


def test_no_calendar_tile_without_calendars(monkeypatch):
    nav = _nav([], monkeypatch=monkeypatch)
    assert not any(a.kind is ActionKind.OPEN_CALENDAR for a in nav.key_map.values())


def test_short_press_opens_the_agenda_with_only_enabled_calendars(monkeypatch):
    asked = []

    def on_events(entity_ids, days):
        asked.append((sorted(entity_ids), days))
        return {"calendar.personal": [
            {"summary": "Dentist", "start": "2026-08-03T13:00:00+00:00",
             "end": "2026-08-03T14:00:00+00:00"},
        ]}

    nav = _nav(_two_calendars(), on_calendar_events=on_events, monkeypatch=monkeypatch)
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    key = _calendar_key(nav)
    nav.handle_press(key, True)
    clock["t"] += 0.1
    nav.handle_press(key, False)

    assert asked == [(["calendar.personal", "calendar.work"], AGENDA_DAYS)]
    assert nav.stack[-1].kind is FrameKind.CALENDAR
    assert [e.summary for e in nav.stack[-1].events] == ["Dentist"]
    assert any(a.kind is ActionKind.CALENDAR_EVENT for a in nav.key_map.values())


def test_agenda_falls_back_to_entity_events_when_the_service_fails(monkeypatch):
    def boom(entity_ids, days):
        raise RuntimeError("no calendar integration")

    nav = _nav(_two_calendars(), on_calendar_events=boom, monkeypatch=monkeypatch)
    nav._open_calendar()
    assert [e.summary for e in nav.stack[-1].events] == ["Dentist", "Standup"]


def test_long_press_opens_the_picker(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    clock = {"t": 100.0}
    monkeypatch.setattr(nav_mod.time, "monotonic", lambda: clock["t"])
    key = _calendar_key(nav)
    nav.handle_press(key, True)
    clock["t"] += 1.0
    nav.handle_press(key, False)

    assert nav.stack[-1].kind is FrameKind.CALENDAR_PICKER
    labels = [a.data["label"] for a in nav.key_map.values() if a.kind is ActionKind.CALENDAR_TOGGLE]
    assert labels == ["Personal", "Work"]


def test_picker_defaults_to_every_calendar_selected(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.stack.append(Frame(FrameKind.CALENDAR_PICKER))
    nav.key_map = nav._build_key_map()
    toggles = [a for a in nav.key_map.values() if a.kind is ActionKind.CALENDAR_TOGGLE]
    assert all(a.data["active"] for a in toggles)


def test_toggling_a_calendar_stays_in_the_picker_and_persists(monkeypatch, tmp_path):
    import json

    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.stack.append(Frame(FrameKind.CALENDAR_PICKER))
    nav.key_map = nav._build_key_map()
    key = next(k for k, a in nav.key_map.items()
               if a.kind is ActionKind.CALENDAR_TOGGLE and a.data["entity_id"] == "calendar.work")
    nav.handle_press(key, True)

    assert nav.stack[-1].kind is FrameKind.CALENDAR_PICKER  # still in the menu
    active = {a.data["entity_id"]: a.data["active"] for a in nav.key_map.values()
              if a.kind is ActionKind.CALENDAR_TOGGLE}
    assert active == {"calendar.personal": True, "calendar.work": False}

    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["calendars"] == ["calendar.personal"]


def test_a_saved_selection_is_honoured_on_startup(monkeypatch, tmp_path):
    import json

    (tmp_path / "state.json").write_text(json.dumps({"calendars": ["calendar.work"]}))
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    assert [c.entity_id for c in nav._active_calendars()] == ["calendar.work"]
    # The tile now shows Work's event rather than the sooner Personal one.
    assert nav._next_event().summary == "Standup"


def test_deselecting_everything_leaves_the_tile_empty(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.stack.append(Frame(FrameKind.CALENDAR_PICKER))
    nav.key_map = nav._build_key_map()
    for entity_id in ("calendar.personal", "calendar.work"):
        key = next(k for k, a in nav.key_map.items()
                   if a.kind is ActionKind.CALENDAR_TOGGLE and a.data["entity_id"] == entity_id)
        nav.handle_press(key, True)
    assert nav._next_event() is None


def test_update_calendar_repaints_the_home_tile(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.render()
    key = _calendar_key(nav)
    nav.display.images.clear()

    changed = nav.update_calendar(
        "calendar.personal", "on",
        _attrs("Dentist moved", "2026-08-03 12:30:00", "2026-08-03 13:30:00", name="Personal"))
    assert changed is True
    assert key in nav.display.images
    assert nav._next_event().summary == "Dentist moved"


def test_update_calendar_ignores_unknown_entities(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    assert nav.update_calendar("calendar.someone_else", "on", {}) is False


def test_tick_redraws_the_calendar_tile_only_when_its_text_changes(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.render()
    key = _calendar_key(nav)

    nav.tick()  # first tick after a render primes the change-detection state
    nav.display.images.clear()
    nav.tick()
    assert key not in nav.display.images  # nothing changed

    _freeze(nav, monkeypatch, NOW + timedelta(hours=3))  # the 13:00 event is now ongoing
    nav.tick()
    assert key in nav.display.images


# -- agenda layout: one column per day ----------------------------------------


def _day(label, date_label, *summaries):
    return CalendarDay(label, date_label, [
        CalendarEvent("calendar.a", "A", s, 0.0, 1.0, clock="09:00") for s in summaries
    ])


def _columns(key_map, cols=8, rows=4):
    """The agenda grid as {column: [header/event labels top to bottom]}."""
    grid = {}
    for col in range(1, cols):
        cells = []
        for row in range(rows):
            action = key_map.get(row * cols + col)
            if action is None:
                cells.append(None)
            elif action.kind is ActionKind.CALENDAR_DAY:
                cells.append(f"{action.data['label']} {action.data['date']}")
            elif action.kind is ActionKind.CALENDAR_EVENT:
                cells.append(action.event.summary)
            else:
                cells.append(action.kind.name)
        grid[col] = cells
    return grid


def test_each_day_gets_a_column_with_a_header_on_top():
    days = [_day("Mon", "Aug 03", "Dentist", "Dinner"), _day("Tue", "Aug 04", "Standup")]
    grid = _columns(layout_calendar(days, 32, 8, 0))
    assert grid[1] == ["Mon Aug 03", "Dentist", "Dinner", None]
    assert grid[2] == ["Tue Aug 04", "Standup", None, None]
    assert grid[3] == [None, None, None, None]


def test_back_keeps_column_zero():
    layout = layout_calendar([_day("Mon", "Aug 03", "Dentist")], 32, 8, 0)
    assert layout[0].kind is ActionKind.BACK
    assert all(k % 8 != 0 or k == 0 for k in layout)  # nothing else in column 0


def test_a_day_with_no_events_still_gets_its_column():
    days = [_day("Mon", "Aug 03", "Dentist"), _day("Tue", "Aug 04"), _day("Wed", "Aug 05", "Standup")]
    grid = _columns(layout_calendar(days, 32, 8, 0))
    assert grid[1] == ["Mon Aug 03", "Dentist", None, None]
    assert grid[2] == ["Tue Aug 04", None, None, None]  # header, nothing under it
    assert grid[3] == ["Wed Aug 05", "Standup", None, None]


def test_a_busy_day_spills_into_the_next_column_under_the_same_header():
    days = [_day("Mon", "Aug 03", "A", "B", "C", "D"), _day("Tue", "Aug 04", "E")]
    grid = _columns(layout_calendar(days, 32, 8, 0))
    assert grid[1] == ["Mon Aug 03", "A", "B", "C"]
    assert grid[2] == ["Mon Aug 03", "D", None, None]  # header repeats on the spill
    assert grid[3] == ["Tue Aug 04", "E", None, None]


def test_more_days_than_columns_paginate():
    days = [_day("D%d" % i, "Aug %02d" % (3 + i), "E%d" % i) for i in range(9)]
    first = layout_calendar(days, 32, 8, 0)
    headers = [a.data["label"] for k, a in sorted(first.items()) if a.kind is ActionKind.CALENDAR_DAY]
    assert headers == ["D0", "D1", "D2", "D3", "D4", "D5", "D6"]  # 7 content columns
    assert first[3 * 8].kind is ActionKind.PAGE and first[3 * 8].delta == 1  # Next, column 0

    second = layout_calendar(days, 32, 8, 1)
    headers = [a.data["label"] for k, a in sorted(second.items()) if a.kind is ActionKind.CALENDAR_DAY]
    assert headers == ["D7", "D8"]
    assert second[2 * 8].delta == -1  # Prev
    assert 3 * 8 not in second        # no Next on the last page


def test_only_todays_column_shows_the_countdown():
    days = [_day("Mon", "Aug 03", "Dentist"), _day("Tue", "Aug 04", "Standup")]
    layout = layout_calendar(days, 32, 8, 0)
    today = layout[1 * 8 + 1]
    tomorrow = layout[1 * 8 + 2]
    assert today.data["today"] is True
    assert tomorrow.data["today"] is False


def test_agenda_columns_render_from_real_frames(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch, on_calendar_events=lambda ids, days: {
        "calendar.personal": [
            {"summary": "Dentist", "start": "2026-08-03T13:00:00+00:00",
             "end": "2026-08-03T14:00:00+00:00"},
            {"summary": "Standup", "start": "2026-08-04T09:30:00+00:00",
             "end": "2026-08-04T10:00:00+00:00"},
        ]})
    nav._open_calendar()
    grid = _columns(nav.key_map)
    assert grid[1] == ["Mon Aug 03", "Dentist", None, None]
    assert grid[2] == ["Tue Aug 04", "Standup", None, None]
    assert grid[3] == ["Wed Aug 05", None, None, None]  # the rest of the week is still there
    assert grid[7] == ["Sun Aug 09", None, None, None]


def test_day_headers_are_not_interactive(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch,
               on_calendar_events=lambda ids, days: {"calendar.personal": [
                   {"summary": "Dentist", "start": "2026-08-03T13:00:00+00:00",
                    "end": "2026-08-03T14:00:00+00:00"}]})
    nav._open_calendar()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.CALENDAR_DAY)
    depth = len(nav.stack)
    nav.handle_press(key, True)
    nav.handle_press(key, False)
    assert len(nav.stack) == depth


def test_the_agenda_horizon_defaults_to_two_weeks():
    assert AGENDA_DAYS == 14


def test_a_configured_horizon_drives_both_the_fetch_and_the_columns(monkeypatch):
    asked = []

    def on_events(entity_ids, days):
        asked.append(days)
        return {}

    nav = _nav(_two_calendars(), on_calendar_events=on_events, monkeypatch=monkeypatch,
               agenda_days=3)
    nav._open_calendar()
    assert asked == [3]
    headers = [a.data["date"] for k, a in sorted(nav.key_map.items())
               if a.kind is ActionKind.CALENDAR_DAY]
    assert headers == ["Aug 03", "Aug 04", "Aug 05"]


def test_a_horizon_longer_than_the_deck_paginates(monkeypatch):
    nav = _nav(_two_calendars(), on_calendar_events=lambda ids, days: {},
               monkeypatch=monkeypatch, agenda_days=14)
    nav._open_calendar()
    first = [a.data["date"] for k, a in sorted(nav.key_map.items())
             if a.kind is ActionKind.CALENDAR_DAY]
    assert first == ["Aug 03", "Aug 04", "Aug 05", "Aug 06", "Aug 07", "Aug 08", "Aug 09"]
    nav._change_page(1)
    second = [a.data["date"] for k, a in sorted(nav.key_map.items())
              if a.kind is ActionKind.CALENDAR_DAY]
    assert second == ["Aug 10", "Aug 11", "Aug 12", "Aug 13", "Aug 14", "Aug 15", "Aug 16"]


def test_an_empty_agenda_still_shows_the_week(monkeypatch):
    nav = _nav([_calendar("calendar.a", "A")], on_calendar_events=lambda ids, days: {},
               monkeypatch=monkeypatch)
    nav._open_calendar()
    headers = [a.data["label"] for k, a in sorted(nav.key_map.items())
               if a.kind is ActionKind.CALENDAR_DAY]
    assert headers == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert not any(a.kind is ActionKind.CALENDAR_EVENT for a in nav.key_map.values())


def test_agenda_tiles_are_not_interactive(monkeypatch):
    nav = _nav(_two_calendars(), on_calendar_events=lambda ids, days: {}, monkeypatch=monkeypatch)
    nav._open_calendar()
    key = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.CALENDAR_EVENT)
    depth = len(nav.stack)
    nav.handle_press(key, True)
    nav.handle_press(key, False)
    assert len(nav.stack) == depth


def test_back_leaves_the_picker(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav.stack.append(Frame(FrameKind.CALENDAR_PICKER))
    nav.key_map = nav._build_key_map()
    nav.handle_press(0, True)
    assert nav.stack[-1].kind is FrameKind.HOME


def test_set_model_swaps_calendars_but_keeps_the_selection(monkeypatch):
    nav = _nav(_two_calendars(), monkeypatch=monkeypatch)
    nav._enabled_calendars = {"calendar.work"}
    nav.set_model(nav.rooms, [], [], None, _two_calendars())
    assert [c.entity_id for c in nav._active_calendars()] == ["calendar.work"]


# -- rendering ----------------------------------------------------------------


@requires_assets
def test_views_render(monkeypatch):
    nav = _nav(_two_calendars(), on_calendar_events=lambda ids, days: {}, monkeypatch=monkeypatch)
    nav.render()                                   # home, with the calendar tile
    nav._open_calendar()                           # agenda
    nav._pop()
    nav.stack.append(Frame(FrameKind.CALENDAR_PICKER))
    nav.render()                                   # picker


@requires_assets
def test_calendar_tile_differs_with_and_without_an_event():
    renderer = KeyRenderer((96, 96))
    event = next_event([_calendar("calendar.a", "A", "Dentist",
                                  "2026-08-03 13:00:00", "2026-08-03 14:00:00")], NOW, TZ)
    assert renderer.calendar_button(event).tobytes() != renderer.calendar_button(None).tobytes()
