"""Stream Deck hardware control: open the device, push key images, route presses.

Exposes a small ``set_image(key, image)`` / ``key_count`` surface so the
navigation layer can drive either real hardware (this class) or the offscreen
export display without caring which.

The deck can be unplugged and replugged at runtime: a watchdog detects the
removal (writes become no-ops meanwhile), re-opens the device when it returns,
re-registers the key callback, and redraws the current view.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PIL import Image
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

logger = logging.getLogger(__name__)

# fn(key_index: int, pressed: bool)
PressCallback = Callable[[int, bool], None]

WATCHDOG_INTERVAL_S = 2.0


class DeckController:
    def __init__(self, brightness: int = 60) -> None:
        self._brightness = brightness
        self._lock = threading.Lock()
        self.deck = None
        self._press_callback: PressCallback | None = None
        self._on_reconnect: Callable[[], None] | None = None
        # Geometry is cached from the first open so key_count/key_size keep
        # working while the deck is unplugged.
        self.key_size: tuple[int, int] = (96, 96)
        self.cols: int = 8
        self._key_count: int = 32

        if not self._open_locked():
            raise RuntimeError(
                "No Stream Deck found. Check the USB connection and (on Linux) the udev rule."
            )

    # -- open / close -------------------------------------------------------

    def _open_locked(self) -> bool:
        """Open the first enumerated deck. Returns False if none is present."""
        decks = DeviceManager().enumerate()
        if not decks:
            return False
        deck = decks[0]
        deck.open()
        deck.reset()
        deck.set_brightness(self._brightness)
        self.key_size = tuple(deck.key_image_format()["size"])
        self.cols = deck.key_layout()[1]
        self._key_count = deck.key_count()
        if self._press_callback is not None:
            cb = self._press_callback
            deck.set_key_callback(lambda _deck, key, pressed: cb(key, pressed))
        self.deck = deck
        logger.info("Opened %s (%d keys)", deck.deck_type(), self._key_count)
        return True

    def _teardown_locked(self) -> None:
        deck, self.deck = self.deck, None
        if deck is not None:
            try:
                deck.close()
            except Exception:  # noqa: BLE001 - the handle is likely already dead
                pass

    # -- display / input surface -------------------------------------------

    @property
    def key_count(self) -> int:
        return self._key_count

    def set_image(self, key: int, image: Image.Image) -> None:
        with self._lock:
            deck = self.deck
            if deck is None:  # unplugged: drop the frame, the watchdog will redraw
                return
            try:
                deck.set_key_image(key, PILHelper.to_native_key_format(deck, image))
            except Exception:  # noqa: BLE001 - a failed write means it was unplugged
                self._teardown_locked()

    def set_callback(self, callback: PressCallback) -> None:
        self._press_callback = callback
        with self._lock:
            if self.deck is not None:
                self.deck.set_key_callback(lambda _deck, key, pressed: callback(key, pressed))

    def set_reconnect_callback(self, callback: Callable[[], None]) -> None:
        """Called (off-lock) after the deck is re-opened, to redraw the view."""
        self._on_reconnect = callback

    def set_brightness(self, brightness: int) -> None:
        self._brightness = brightness
        with self._lock:
            if self.deck is not None:
                try:
                    self.deck.set_brightness(brightness)
                except Exception:  # noqa: BLE001
                    self._teardown_locked()

    def close(self) -> None:
        with self._lock:
            if self.deck is not None:
                try:
                    self.deck.reset()
                except Exception:  # noqa: BLE001 - best-effort cleanup
                    pass
            self._teardown_locked()

    # -- reconnection watchdog ---------------------------------------------

    def _present(self) -> bool:
        try:
            return len(DeviceManager().enumerate()) > 0
        except Exception:  # noqa: BLE001
            return False

    def _watchdog_once(self) -> None:
        """One check: probe the open deck, or try to re-open a returned one."""
        reconnected = False
        with self._lock:
            deck = self.deck
            if deck is not None:
                try:
                    deck.get_firmware_version()  # live USB read; raises if unplugged
                except Exception:  # noqa: BLE001
                    logger.warning("Stream Deck disconnected; waiting for it to return")
                    self._teardown_locked()
                return
        # disconnected: re-open when a deck reappears (enumerate outside the lock)
        if self._present():
            with self._lock:
                reconnected = self.deck is None and self._open_locked()
        if reconnected:
            logger.info("Stream Deck reconnected")
            if self._on_reconnect is not None:
                try:
                    self._on_reconnect()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Redraw after reconnect failed: %s", exc)

    def run_watchdog(self, stop_event: threading.Event) -> None:
        """Blocking loop (run in a thread): detect unplug/replug and recover."""
        while not stop_event.is_set():
            try:
                self._watchdog_once()
            except Exception as exc:  # noqa: BLE001 - keep the watchdog alive
                logger.warning("Deck watchdog error: %s", exc)
            stop_event.wait(WATCHDOG_INTERVAL_S)
