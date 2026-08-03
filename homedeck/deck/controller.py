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

from .. import state

logger = logging.getLogger(__name__)

# fn(key_index: int, pressed: bool)
PressCallback = Callable[[int, bool], None]

WATCHDOG_INTERVAL_S = 2.0

# Brightness levels the Settings "Brightness" button cycles through (percent).
BRIGHTNESS_LEVELS = (20, 40, 60, 80, 100)

# Per-key image rotation for a clockwise display rotation of k*90°.
_TRANSPOSE = {1: Image.ROTATE_270, 2: Image.ROTATE_180, 3: Image.ROTATE_90}


def _deck_id(deck) -> str | None:
    """A stable identifier (USB path) for a deck, available before opening."""
    try:
        return deck.id()
    except Exception:  # noqa: BLE001 - not all backends expose it
        return None


class DeckController:
    def __init__(self, brightness: int = 60, rotation_degrees: int = 0) -> None:
        self._brightness = brightness  # configured "on" brightness
        self._display_on = True        # backlight state (occupancy can toggle it)
        self._lock = threading.Lock()
        self.deck = None
        self._press_callback: PressCallback | None = None
        self._on_reconnect: Callable[[], None] | None = None
        self._device_id: str | None = None
        # Physical geometry is cached from the first open so key_count/key_size
        # keep working while the deck is unplugged.
        self.key_size: tuple[int, int] = (96, 96)
        self._phys_rows, self._phys_cols, self._phys_key_count = 4, 8, 32
        self._rotation_k = (int(rotation_degrees) // 90) % 4
        self._build_maps()

        if not self._open_locked():
            raise RuntimeError(
                "No Stream Deck found. Check the USB connection and (on Linux) the udev rule."
            )

    # -- rotation -----------------------------------------------------------

    def _build_maps(self) -> None:
        """(Re)compute logical<->physical key maps for the current rotation."""
        pr, pc = self._phys_rows, self._phys_cols
        grid = [[r * pc + c for c in range(pc)] for r in range(pr)]
        # Rotate the index grid 90° counter-clockwise per step: this makes
        # logical->physical apply the *same* clockwise rotation as the per-key
        # image transpose, so the whole framebuffer rotates as one rigid piece
        # (without this, arrangement and content differ by 180° and the order
        # comes out reversed in portrait).
        for _ in range(self._rotation_k):
            grid = [list(row) for row in zip(*grid)][::-1]
        self._logical_cols = len(grid[0]) if grid else pc
        self._log_to_phys: dict[int, int] = {}
        self._phys_to_log: dict[int, int] = {}
        for lr, row in enumerate(grid):
            for lc, phys in enumerate(row):
                logical = lr * self._logical_cols + lc
                self._log_to_phys[logical] = phys
                self._phys_to_log[phys] = logical

    @property
    def rotation(self) -> int:
        return self._rotation_k * 90

    def set_rotation(self, degrees: int) -> None:
        """Rotate the display to 0/90/180/270°; remaps keys, persists, redraws."""
        with self._lock:
            self._rotation_k = (int(degrees) // 90) % 4
            self._build_maps()
            if self.deck is not None:
                self._register_callback_locked(self.deck)
        state.save({**state.load(), "rotation": self.rotation})
        if self._on_reconnect is not None:
            try:
                self._on_reconnect()  # rebuild the view for the new column count
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redraw after rotate failed: %s", exc)

    def cycle_rotation(self) -> None:
        self.set_rotation(self.rotation + 90)

    # -- open / close -------------------------------------------------------

    def _open_locked(self, deck=None) -> bool:
        """Open a deck (given or the first enumerated). False if none is present."""
        if deck is None:
            decks = DeviceManager().enumerate()
            if not decks:
                return False
            deck = decks[0]
        deck.open()
        deck.reset()
        # Respect the current backlight state so a reconnect while "off" stays off.
        deck.set_brightness(self._brightness if self._display_on else 0)
        self.key_size = tuple(deck.key_image_format()["size"])
        self._phys_rows, self._phys_cols = deck.key_layout()
        self._phys_key_count = deck.key_count()
        self._build_maps()
        self._device_id = _deck_id(deck)
        self._register_callback_locked(deck)
        self.deck = deck
        logger.info("Opened %s (%d keys, rotation %d°)", deck.deck_type(), self._phys_key_count, self.rotation)
        return True

    def _register_callback_locked(self, deck) -> None:
        cb = self._press_callback
        if cb is None:
            return
        # The deck reports physical key indices; map them back to logical ones.
        deck.set_key_callback(lambda _deck, phys, pressed: cb(self._phys_to_log.get(phys, phys), pressed))

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
        return self._phys_key_count

    @property
    def cols(self) -> int:
        return self._logical_cols

    def set_image(self, key: int, image: Image.Image) -> None:
        with self._lock:
            deck = self.deck
            if deck is None:  # unplugged: drop the frame, the watchdog will redraw
                return
            phys = self._log_to_phys.get(key, key)
            if self._rotation_k:
                image = image.transpose(_TRANSPOSE[self._rotation_k])
            try:
                deck.set_key_image(phys, PILHelper.to_native_key_format(deck, image))
            except Exception:  # noqa: BLE001 - a failed write means it was unplugged
                self._teardown_locked()

    def set_callback(self, callback: PressCallback) -> None:
        self._press_callback = callback
        with self._lock:
            if self.deck is not None:
                self._register_callback_locked(self.deck)

    def set_reconnect_callback(self, callback: Callable[[], None]) -> None:
        """Called (off-lock) after the deck is re-opened, to redraw the view."""
        self._on_reconnect = callback

    @property
    def brightness(self) -> int:
        return self._brightness

    def set_brightness(self, brightness: int) -> None:
        with self._lock:
            self._brightness = brightness
            self._apply_brightness_locked()

    def cycle_brightness(self) -> None:
        """Step to the next preset brightness level, persist it, and redraw."""
        nxt = next((lvl for lvl in BRIGHTNESS_LEVELS if lvl > self._brightness), BRIGHTNESS_LEVELS[0])
        self.set_brightness(nxt)
        state.save({**state.load(), "brightness": nxt})
        if self._on_reconnect is not None:
            try:
                self._on_reconnect()  # redraw so the Settings label shows the new value
            except Exception as exc:  # noqa: BLE001
                logger.warning("Redraw after brightness change failed: %s", exc)

    def set_display_on(self, on: bool) -> None:
        """Turn the deck's backlight on/off (e.g. following an occupancy sensor).

        The configured brightness is preserved, so turning the display back on
        restores it. A no-op if the state is unchanged or the deck is unplugged.
        """
        with self._lock:
            if on == self._display_on:
                return
            self._display_on = on
            logger.info("Display %s", "on" if on else "off")
            self._apply_brightness_locked()

    def _apply_brightness_locked(self) -> None:
        if self.deck is None:  # unplugged: applied on the next open
            return
        try:
            self.deck.set_brightness(self._brightness if self._display_on else 0)
        except Exception:  # noqa: BLE001 - a failed write means it was unplugged
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

    def _watchdog_once(self) -> None:
        """One check, driven by USB enumeration (no blocking reads on our handle).

        Enumeration runs off-lock so a wedged bus can never freeze presses; the
        open deck is considered gone the moment it stops being enumerated.
        """
        try:
            enumerated = DeviceManager().enumerate()
        except Exception as exc:  # noqa: BLE001 - treat an enumeration error as "no decks"
            logger.debug("Deck enumeration failed: %s", exc)
            enumerated = []
        present_ids = {_deck_id(d) for d in enumerated}

        reconnected = False
        with self._lock:
            if self.deck is not None:
                # Still on the bus? (None id -> can't tell, trust write failures instead.)
                if self._device_id is not None and self._device_id not in present_ids:
                    logger.warning("Stream Deck disconnected; waiting for it to return")
                    self._teardown_locked()
                return
            if enumerated:  # returned to the bus -> re-open it
                reconnected = self._open_locked(enumerated[0])

        if reconnected:
            logger.warning("Stream Deck reconnected")
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
