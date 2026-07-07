"""HomeDeck entrypoint: connect to HA, build rooms, drive the Stream Deck.

  homedeck                 run against an attached Stream Deck XL
  homedeck --export DIR    render each room's keys to PNG grids (no hardware)
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import Config
from .export import ExportDisplay, export_views
from .ha.client import HaClient
from .ha.model import DeviceEntity, Floor, Room, build_rooms, group_by_floor
from .ha.weather import Weather
from .ui.navigation import Navigation

logger = logging.getLogger("homedeck")


def _find_weather(states: dict[str, dict], preferred: str | None) -> Weather | None:
    """Pick the weather entity (the configured one, else the first weather.*)."""
    candidates = sorted(eid for eid in states if eid.startswith("weather."))
    entity_id = preferred if (preferred in states) else (candidates[0] if candidates else None)
    if entity_id is None:
        return None
    info = states[entity_id]
    return Weather.from_state(entity_id, info.get("state", ""), info.get("attributes", {}))


def _load_model(client: HaClient, weather_pref: str | None) -> tuple[list[Room], list[Floor], list[Room], Weather | None]:
    """Load rooms (grouped by HA floor) and the weather entity.

    Returns (all_rooms, floors, unassigned_rooms, weather). ``floors`` is empty
    when HA has no floors (home screen shows rooms flat); ``weather`` is None
    when no weather entity exists.
    """
    areas = client.get_areas()
    entity_reg = client.get_entity_registry()
    device_reg = client.get_device_registry()
    states = client.get_states()
    rooms = build_rooms(areas, entity_reg, device_reg, states)

    floors, unassigned = group_by_floor(client.get_floor_registry(), rooms)
    weather = _find_weather(states, weather_pref)
    total_entities = sum(len(r.entities) for r in rooms)
    logger.info(
        "Loaded %d room(s) with %d device(s) across %d floor(s); weather: %s",
        len(rooms), total_entities, len(floors), weather.entity_id if weather else "none",
    )
    return rooms, floors, unassigned, weather


def _resolve_timezone(client: HaClient, fallback: str | None):
    """HA's configured timezone (preferred). None → use the container's TZ."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    name = None
    try:
        name = client.get_config().get("time_zone")
    except Exception as exc:  # noqa: BLE001 - timezone is best-effort
        logger.info("Could not read HA timezone (%s)", exc)
    for candidate in (name, fallback):
        if not candidate:
            continue
        try:
            tz = ZoneInfo(candidate)
            logger.info("Using timezone %s for history", candidate)
            return tz
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("Unknown timezone %r; ignoring", candidate)
    return None  # fall back to the container's local time (TZ env)


def _index_entities(rooms: list[Room]) -> dict[str, DeviceEntity]:
    index: dict[str, DeviceEntity] = {}
    for room in rooms:
        for entity in room.entities:
            index[entity.entity_id] = entity
    return index


def run_export(config: Config, out_dir: str) -> int:
    from .deck.renderer import KeyRenderer

    client = HaClient(config.ha_url, config.ha_token)
    client.connect()
    try:
        rooms, floors, unassigned, weather = _load_model(client, config.weather_entity)
    finally:
        client.close()

    if not rooms:
        logger.warning("No rooms with in-scope devices found. Nothing to export.")
        return 1

    display = ExportDisplay()
    renderer = KeyRenderer(display.key_size)
    navigation = Navigation(
        display, renderer, rooms, on_service=lambda _e: None,
        floors=floors, unassigned_rooms=unassigned, weather=weather,
    )
    written = export_views(rooms, navigation, display, out_dir)
    for path in written:
        logger.info("wrote %s", path)
    return 0


def run_deck(config: Config) -> int:
    from .deck.controller import DeckController
    from .deck.renderer import KeyRenderer

    client = HaClient(config.ha_url, config.ha_token)
    client.connect()
    rooms, floors, unassigned, weather = _load_model(client, config.weather_entity)
    if not rooms:
        logger.warning("No rooms with in-scope devices found. Check your HA areas.")

    deck = DeckController(brightness=config.brightness)
    renderer = KeyRenderer(deck.key_size)

    def on_service(call: tuple[str, str, str, dict]) -> None:
        client.call_service(*call)

    entity_index = _index_entities(rooms)

    def reload_model() -> None:
        """Re-fetch areas/entities/floors/weather from HA and swap them in."""
        r, f, u, w = _load_model(client, config.weather_entity)
        entity_index.clear()
        entity_index.update(_index_entities(r))
        navigation.set_model(r, f, u, w)
        logger.info("Reloaded configuration from Home Assistant")

    navigation = Navigation(
        deck, renderer, rooms, on_service=on_service,
        floors=floors, unassigned_rooms=unassigned,
        weather=weather, on_forecast=client.get_forecast,
        on_logbook=client.get_logbook, on_reload=reload_model,
        tz=_resolve_timezone(client, config.timezone),
    )

    def on_state_changed(entity_id: str, state: str, attributes: dict) -> None:
        w = navigation.weather
        if w is not None and entity_id == w.entity_id:
            navigation.update_weather(state, attributes)
            return
        entity = entity_index.get(entity_id)
        if entity is None:
            return
        entity.update_from_state(state, attributes)
        navigation.refresh_entity(entity_id)

    def on_connection(connected: bool) -> None:
        navigation.set_connected(connected)

    deck.set_callback(navigation.handle_press)
    deck.set_reconnect_callback(navigation.render)  # redraw when the deck is replugged
    navigation.render()

    stop_event = threading.Event()
    listener = threading.Thread(
        target=client.listen,
        args=(on_state_changed, stop_event, on_connection),
        name="ha-event-listener",
        daemon=True,
    )
    listener.start()

    # Watch for the deck being unplugged/replugged and recover automatically.
    threading.Thread(
        target=deck.run_watchdog, args=(stop_event,), name="deck-watchdog", daemon=True
    ).start()

    # Tick active timers once a second so their remaining time counts down live.
    # (Always on: a reload may introduce timers; tick() is a cheap no-op otherwise.)
    def tick_loop() -> None:
        while not stop_event.is_set():
            navigation.tick()
            stop_event.wait(1.0)

    threading.Thread(target=tick_loop, name="timer-ticker", daemon=True).start()

    def shutdown(*_args) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    logger.info("HomeDeck running. Press Ctrl+C to exit.")
    try:
        stop_event.wait()
    finally:
        deck.close()
        client.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="homedeck", description="Control Home Assistant from a Stream Deck XL")
    parser.add_argument("--export", metavar="DIR", help="render room views to PNG grids in DIR and exit (no hardware)")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="verbose (INFO) logging; without it only warnings and errors are shown")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # These libraries log every message/request at INFO/DEBUG — keep them quiet
    # even in verbose mode.
    for noisy in ("homeassistant_api", "websockets", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = Config.from_env()
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    try:
        if args.export:
            return run_export(config, args.export)
        return run_deck(config)
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - top-level guard with a clear message
        logger.error("Fatal: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
