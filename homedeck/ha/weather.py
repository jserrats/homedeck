"""Weather entity + forecast model (a HA ``weather.*`` entity)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# HA weather condition -> MDI icon name.
CONDITION_ICONS: dict[str, str] = {
    "clear-night": "weather-night",
    "cloudy": "weather-cloudy",
    "exceptional": "weather-hazy",
    "fog": "weather-fog",
    "hail": "weather-hail",
    "lightning": "weather-lightning",
    "lightning-rainy": "weather-lightning-rainy",
    "partlycloudy": "weather-partly-cloudy",
    "pouring": "weather-pouring",
    "rainy": "weather-rainy",
    "snowy": "weather-snowy",
    "snowy-rainy": "weather-snowy-rainy",
    "sunny": "weather-sunny",
    "windy": "weather-windy",
    "windy-variant": "weather-windy-variant",
}
DEFAULT_CONDITION_ICON = "weather-cloudy"


def condition_icon(condition: str | None) -> str:
    return CONDITION_ICONS.get((condition or "").lower(), DEFAULT_CONDITION_ICON)


@dataclass
class ForecastDay:
    label: str          # weekday abbreviation, e.g. "Wed"
    icon: str           # MDI icon name for the condition
    high: float | None
    low: float | None

    def temp_text(self) -> str:
        hi = f"{round(self.high)}°" if self.high is not None else "—"
        lo = f"{round(self.low)}°" if self.low is not None else "—"
        return f"{hi}/{lo}"


@dataclass
class Weather:
    entity_id: str
    condition: str
    temperature: float | None

    @property
    def icon(self) -> str:
        return condition_icon(self.condition)

    def temp_text(self) -> str:
        return f"{round(self.temperature)}°" if self.temperature is not None else "—"

    def update(self, state: str, attributes: dict) -> None:
        self.condition = state
        temp = attributes.get("temperature")
        self.temperature = float(temp) if temp is not None else None

    @classmethod
    def from_state(cls, entity_id: str, state: str, attributes: dict) -> "Weather":
        temp = attributes.get("temperature")
        return cls(entity_id, state, float(temp) if temp is not None else None)


def _weekday_label(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        # Handle a trailing Z and offset forms.
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]


def parse_forecast(raw: list[dict]) -> list[ForecastDay]:
    """Turn HA daily-forecast dicts into ForecastDay tiles."""
    days: list[ForecastDay] = []
    for item in raw or []:
        high = item.get("temperature")
        low = item.get("templow")
        days.append(
            ForecastDay(
                label=_weekday_label(item.get("datetime")),
                icon=condition_icon(item.get("condition")),
                high=float(high) if high is not None else None,
                low=float(low) if low is not None else None,
            )
        )
    return days
