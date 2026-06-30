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
from .ui.navigation import Navigation

logger = logging.getLogger("homedeck")


def _load_model(client: HaClient) -> tuple[list[Room], list[Floor], list[Room]]:
    """Load rooms and group them by HA floor.

    Returns (all_rooms, floors, unassigned_rooms). ``floors`` is empty when HA
    has no floors, in which case the home screen shows rooms flat.
    """
    areas = client.get_areas()
    entity_reg = client.get_entity_registry()
    device_reg = client.get_device_registry()
    states = client.get_states()
    rooms = build_rooms(areas, entity_reg, device_reg, states)

    floors, unassigned = group_by_floor(client.get_floor_registry(), rooms)
    total_entities = sum(len(r.entities) for r in rooms)
    logger.info(
        "Loaded %d room(s) with %d device(s) across %d floor(s)",
        len(rooms), total_entities, len(floors),
    )
    return rooms, floors, unassigned


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
        rooms, floors, unassigned = _load_model(client)
    finally:
        client.close()

    if not rooms:
        logger.warning("No rooms with in-scope devices found. Nothing to export.")
        return 1

    display = ExportDisplay()
    renderer = KeyRenderer(display.key_size)
    navigation = Navigation(
        display, renderer, rooms, on_service=lambda _e: None,
        floors=floors, unassigned_rooms=unassigned,
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
    rooms, floors, unassigned = _load_model(client)
    if not rooms:
        logger.warning("No rooms with in-scope devices found. Check your HA areas.")

    deck = DeckController(brightness=config.brightness)
    renderer = KeyRenderer(deck.key_size)

    def on_service(call: tuple[str, str, str]) -> None:
        client.call_service(*call)

    navigation = Navigation(
        deck, renderer, rooms, on_service=on_service,
        floors=floors, unassigned_rooms=unassigned,
    )
    entity_index = _index_entities(rooms)

    def on_state_changed(entity_id: str, state: str, attributes: dict) -> None:
        entity = entity_index.get(entity_id)
        if entity is None:
            return
        entity.update_from_state(state, attributes)
        navigation.refresh_entity(entity_id)

    def on_connection(connected: bool) -> None:
        navigation.set_connected(connected)

    deck.set_callback(navigation.handle_press)
    navigation.render()

    stop_event = threading.Event()
    listener = threading.Thread(
        target=client.listen,
        args=(on_state_changed, stop_event, on_connection),
        name="ha-event-listener",
        daemon=True,
    )
    listener.start()

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
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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
