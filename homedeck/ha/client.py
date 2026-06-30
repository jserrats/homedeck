"""Thin wrapper over homeassistant_api's synchronous WebsocketClient.

The library is synchronous and ``listen_events`` blocks while streaming, so we
use two connections:

  * a **command** connection (guarded by a lock) for the initial registry/state
    load and for service calls triggered by key presses, and
  * an **event** connection that runs the ``state_changed`` listen loop on its
    own thread and reconnects on failure.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from homeassistant_api import WebsocketClient

logger = logging.getLogger(__name__)

StateCallback = Callable[[str, str, dict], None]
ConnectionCallback = Callable[[bool], None]

RECONNECT_DELAY_S = 5.0


class HaClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._cmd = WebsocketClient(url, token)
        self._cmd_lock = threading.Lock()
        self._connected = False

    # -- command connection -------------------------------------------------

    def connect(self) -> None:
        """Open the command connection (authenticates). Raises on failure."""
        self._cmd.__enter__()
        self._connected = True
        logger.info("Connected to Home Assistant command channel")

    def close(self) -> None:
        if self._connected:
            try:
                self._cmd.__exit__(None, None, None)
            finally:
                self._connected = False

    def _command(self, msg_type: str, **data) -> object:
        with self._cmd_lock:
            response = self._cmd.recv(self._cmd.send(msg_type, **data))
            return response.result  # type: ignore[attr-defined]

    def get_areas(self) -> list[dict]:
        return self._command("config/area_registry/list")  # type: ignore[return-value]

    def get_floor_registry(self) -> list[dict]:
        """List HA floors. Returns [] if the running HA is too old to support it."""
        try:
            return self._command("config/floor_registry/list")  # type: ignore[return-value]
        except Exception as exc:  # noqa: BLE001 - floors are optional
            logger.info("Floor registry unavailable (%s); continuing without floors", exc)
            return []

    def get_entity_registry(self) -> list[dict]:
        return self._command("config/entity_registry/list")  # type: ignore[return-value]

    def get_device_registry(self) -> list[dict]:
        return self._command("config/device_registry/list")  # type: ignore[return-value]

    def get_states(self) -> dict[str, dict]:
        """Return current states as {entity_id: {"state", "attributes"}}."""
        with self._cmd_lock:
            states = self._cmd.get_states()
        result: dict[str, dict] = {}
        for st in states:
            attrs = st.attributes
            if not isinstance(attrs, dict):
                attrs = dict(attrs) if attrs else {}
            result[st.entity_id] = {"state": st.state, "attributes": attrs}
        return result

    def call_service(self, domain: str, service: str, entity_id: str) -> None:
        with self._cmd_lock:
            self._cmd.trigger_service(domain, service, entity_id=entity_id)

    # -- event connection ---------------------------------------------------

    def listen(
        self,
        on_state_changed: StateCallback,
        stop_event: threading.Event,
        on_connection: ConnectionCallback | None = None,
    ) -> None:
        """Blocking loop (run in a thread): stream state_changed events.

        Reconnects with a fixed delay until ``stop_event`` is set.
        """
        while not stop_event.is_set():
            try:
                with WebsocketClient(self._url, self._token) as ev:
                    if on_connection:
                        on_connection(True)
                    logger.info("Event channel listening for state changes")
                    with ev.listen_events("state_changed") as events:
                        for event in events:
                            if stop_event.is_set():
                                return
                            self._dispatch(event, on_state_changed)
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                if stop_event.is_set():
                    return
                logger.warning("Event channel error: %s; reconnecting in %ss", exc, RECONNECT_DELAY_S)
                if on_connection:
                    on_connection(False)
                stop_event.wait(RECONNECT_DELAY_S)

    @staticmethod
    def _dispatch(event, on_state_changed: StateCallback) -> None:
        data = getattr(event, "data", None) or {}
        entity_id = data.get("entity_id")
        new_state = data.get("new_state")
        if not entity_id or not new_state:
            return  # removed entity or malformed event
        state = new_state.get("state", "unavailable")
        attributes = new_state.get("attributes") or {}
        on_state_changed(entity_id, state, attributes)
