"""Stream Deck hardware control: open the device, push key images, route presses.

Exposes a small ``set_image(key, image)`` / ``key_count`` surface so the
navigation layer can drive either real hardware (this class) or the offscreen
export display without caring which.
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


class DeckController:
    def __init__(self, brightness: int = 60) -> None:
        decks = DeviceManager().enumerate()
        if not decks:
            raise RuntimeError(
                "No Stream Deck found. Check the USB connection and (on Linux) the udev rule."
            )
        self.deck = decks[0]
        self.deck.open()
        self.deck.reset()
        self.deck.set_brightness(brightness)
        self._lock = threading.Lock()
        self.key_size: tuple[int, int] = tuple(self.deck.key_image_format()["size"])  # (w, h)
        logger.info(
            "Opened %s (%d keys, %dx%d)",
            self.deck.deck_type(),
            self.deck.key_count(),
            self.key_size[0],
            self.key_size[1],
        )

    @property
    def key_count(self) -> int:
        return self.deck.key_count()

    def set_image(self, key: int, image: Image.Image) -> None:
        native = PILHelper.to_native_key_format(self.deck, image)
        with self._lock:
            self.deck.set_key_image(key, native)

    def set_callback(self, callback: PressCallback) -> None:
        # StreamDeck passes (deck, key, pressed); adapt to (key, pressed).
        self.deck.set_key_callback(lambda _deck, key, pressed: callback(key, pressed))

    def set_brightness(self, brightness: int) -> None:
        with self._lock:
            self.deck.set_brightness(brightness)

    def is_open(self) -> bool:
        return self.deck.is_open()

    def close(self) -> None:
        with self._lock:
            try:
                self.deck.reset()
                self.deck.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
