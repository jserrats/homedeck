import pytest

from homedeck.deck import icons
from homedeck.deck.renderer import KeyRenderer
from homedeck.export import ExportDisplay
from homedeck.ha.weather import Weather, condition_icon, parse_forecast
from homedeck.ui.navigation import ActionKind, Frame, FrameKind, Navigation

requires_assets = pytest.mark.skipif(not icons.META_PATH.exists(), reason="MDI assets not fetched")

FORECAST = [
    {"datetime": "2026-07-06T12:00:00+00:00", "condition": "sunny", "temperature": 27, "templow": 15},
    {"datetime": "2026-07-07T12:00:00+00:00", "condition": "rainy", "temperature": 21, "templow": 13},
    {"datetime": "2026-07-08T12:00:00+00:00", "condition": "partlycloudy", "temperature": 24, "templow": 14},
]


# -- weather model ------------------------------------------------------------

def test_condition_icon_mapping():
    assert condition_icon("sunny") == "weather-sunny"
    assert condition_icon("partlycloudy") == "weather-partly-cloudy"
    assert condition_icon("clear-night") == "weather-night"
    assert condition_icon("something-unknown") == "weather-cloudy"  # default


def test_weather_from_state_and_update():
    w = Weather.from_state("weather.home", "sunny", {"temperature": 18.4})
    assert w.icon == "weather-sunny"
    assert w.temp_text() == "18°"
    w.update("rainy", {"temperature": 12})
    assert w.condition == "rainy" and w.temp_text() == "12°"


def test_weather_missing_temperature():
    w = Weather.from_state("weather.home", "fog", {})
    assert w.temp_text() == "—"


def test_parse_forecast():
    days = parse_forecast(FORECAST)
    assert len(days) == 3
    assert days[0].label == "Mon"          # 2026-07-06 is a Monday
    assert days[0].icon == "weather-sunny"
    assert days[0].temp_text() == "27°/15°"
    assert days[1].icon == "weather-rainy"


# -- navigation ---------------------------------------------------------------

def _nav(on_forecast=lambda eid: FORECAST):
    weather = Weather("weather.home", "sunny", 18.0)
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None,
                     weather=weather, on_forecast=on_forecast)
    return nav


def test_weather_button_on_home_bottom_row():
    nav = _nav()
    home = nav._build_key_map()
    # bottom row: 24 Lights On, 25 Security, 26 Climate, 27 Weather
    assert home[27].kind is ActionKind.OPEN_WEATHER


def test_no_weather_button_when_no_weather_entity():
    display = ExportDisplay()
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None)
    home = nav._build_key_map()
    assert not any(a.kind is ActionKind.OPEN_WEATHER for a in home.values())


COLS = 8


@requires_assets
def test_pressing_weather_opens_matrix_forecast():
    nav = _nav()
    nav.key_map = nav._build_key_map()
    wkey = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_WEATHER)

    nav.handle_press(wkey, True)
    assert nav.stack[-1].kind is FrameKind.WEATHER

    view = nav._build_key_map()
    cells = [a for a in view.values() if a.kind is ActionKind.WEATHER_CELL]
    assert len(cells) == len(FORECAST) * 4  # one column per day, 4 rows each

    # Back is at key 0 (top-left), day columns start at column 1
    assert view[0].kind is ActionKind.BACK

    # column 1 = first day, top-to-bottom parts: day / icon / max / min
    assert view[1].data["part"] == "day" and view[1].day.label == "Mon"
    assert view[COLS * 1 + 1].data["part"] == "icon"
    assert view[COLS * 2 + 1].data["part"] == "max" and view[COLS * 2 + 1].day.high_text() == "27°"
    assert view[COLS * 3 + 1].data["part"] == "min" and view[COLS * 3 + 1].day.low_text() == "15°"


def test_weather_cells_are_not_interactive():
    nav = _nav()
    nav.stack = [nav.stack[0], Frame(FrameKind.WEATHER, forecast=parse_forecast(FORECAST))]
    nav.key_map = nav._build_key_map()
    nav.handle_press(1, True)  # the first day's label cell (key 0 is Back)
    assert nav.stack[-1].kind is FrameKind.WEATHER  # still on the forecast view


@requires_assets
def test_update_weather_refreshes_button():
    nav = _nav()
    nav.render()  # home
    wkey = next(k for k, a in nav.key_map.items() if a.kind is ActionKind.OPEN_WEATHER)
    before = nav.display.images[wkey].tobytes()
    nav.update_weather("rainy", {"temperature": 5})
    after = nav.display.images[wkey].tobytes()
    assert before != after  # button re-rendered with the new condition/temp


def test_portrait_forecast_is_one_day_per_row():
    from homedeck.ha.weather import parse_forecast
    display = ExportDisplay(cols=4)  # portrait: 4 cols x 8 rows
    nav = Navigation(display, KeyRenderer(display.key_size), [], on_service=lambda c: None,
                     weather=Weather("weather.home", "sunny", 20.0), on_forecast=lambda e: FORECAST)
    nav.stack = [Frame(FrameKind.HOME), Frame(FrameKind.WEATHER, forecast=parse_forecast(FORECAST))]
    view = nav._build_key_map()
    assert view[0].kind is ActionKind.BACK
    # first day occupies row 1 (keys 4..7): day / icon / max / min across the columns
    assert view[4].data["part"] == "day" and view[4].day.label == "Mon"
    assert view[5].data["part"] == "icon"
    assert view[6].data["part"] == "max" and view[6].day.high_text() == "27°"
    assert view[7].data["part"] == "min" and view[7].day.low_text() == "15°"
    # all three days shown (row per day), not truncated to fit columns
    day_cells = [a for a in view.values()
                 if a.kind is ActionKind.WEATHER_CELL and a.data["part"] == "day"]
    assert len(day_cells) == len(FORECAST)
