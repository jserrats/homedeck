"""Thin wrapper over homeassistant_api's synchronous WebsocketClient.

The library is synchronous and ``listen_events`` blocks while streaming, so we
use two connections:

  * a **command** connection (guarded by a lock) for the initial registry/state
    load and for service calls triggered by key presses. It's idle between
    presses, so it can be dropped for missing ping/pongs — every command runs
    through :meth:`_execute`, which transparently reconnects and retries once on
    a dead socket, and
  * an **event** connection that runs the ``state_changed`` listen loop on its
    own thread and reconnects on failure.
"""

from __future__ import annotations

import logging
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from homeassistant_api import WebsocketClient
from homeassistant_api.errors import RequestError

logger = logging.getLogger(__name__)

T = TypeVar("T")

StateCallback = Callable[[str, str, dict], None]
ConnectionCallback = Callable[[bool], None]

RECONNECT_DELAY_S = 5.0


class HaClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token
        self._cmd: WebsocketClient | None = None
        self._cmd_lock = threading.Lock()
        self._connected = False

    # -- command connection -------------------------------------------------

    def connect(self) -> None:
        """Open the command connection (authenticates). Raises on failure."""
        with self._cmd_lock:
            self._open_locked()
        logger.info("Connected to Home Assistant command channel")

    def close(self) -> None:
        with self._cmd_lock:
            self._close_locked()

    def _open_locked(self) -> None:
        self._cmd = WebsocketClient(self._url, self._token)
        self._cmd.__enter__()
        self._connected = True

    def _close_locked(self) -> None:
        if self._cmd is not None:
            try:
                self._cmd.__exit__(None, None, None)
            except Exception:  # noqa: BLE001 - best-effort close
                pass
        self._cmd = None
        self._connected = False

    def _execute(self, fn: Callable[[], T]) -> T:
        """Run a command against the command connection, reconnecting once if the
        socket has died.

        The command connection is idle between key presses, so Home Assistant (or
        a proxy) can close it for missing ping/pongs. A rejected command
        (:class:`RequestError`) is a live connection reporting failure and is
        re-raised as-is; any other error is treated as a dead socket — we
        reconnect and retry once so toggles keep working over long sessions.
        """
        with self._cmd_lock:
            if self._cmd is None:
                self._open_locked()
            try:
                return fn()
            except RequestError:
                raise
            except Exception as exc:  # noqa: BLE001 - assume the socket died
                logger.warning("Command channel error (%s); reconnecting", exc)
                self._close_locked()
                self._open_locked()
                return fn()

    def _command(self, msg_type: str, **data) -> object:
        def run() -> object:
            return self._cmd.recv(self._cmd.send(msg_type, **data)).result  # type: ignore[union-attr]

        return self._execute(run)

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
        states = self._execute(lambda: self._cmd.get_states())  # type: ignore[union-attr]
        result: dict[str, dict] = {}
        for st in states:
            attrs = st.attributes
            if not isinstance(attrs, dict):
                attrs = dict(attrs) if attrs else {}
            result[st.entity_id] = {"state": st.state, "attributes": attrs}
        return result

    def get_forecast(self, entity_id: str, forecast_type: str = "daily") -> list[dict]:
        """Fetch a weather entity's forecast via ``weather.get_forecasts``.

        Returns [] if the entity/HA doesn't support forecast responses.
        """
        def run() -> dict:
            return self._cmd.trigger_service_with_response(  # type: ignore[union-attr]
                "weather", "get_forecasts", type=forecast_type, entity_id=entity_id
            )

        try:
            resp = self._execute(run) or {}
        except Exception as exc:  # noqa: BLE001 - forecast is best-effort
            logger.info("Forecast unavailable for %s (%s)", entity_id, exc)
            return []
        data = resp.get("response", resp) if isinstance(resp, dict) else {}
        entry = data.get(entity_id)
        if entry is None and data:
            entry = next(iter(data.values()))
        forecast = (entry or {}).get("forecast", [])
        return forecast if isinstance(forecast, list) else []

    def get_config(self) -> dict:
        """Home Assistant configuration (includes 'time_zone')."""
        result = self._command("get_config")
        return result if isinstance(result, dict) else {}

    def get_logbook(self, entity_id: str, hours: int = 24) -> list[dict]:
        """Fetch recent logbook events for an entity (state changes + context).

        Returns [] if the logbook isn't available.
        """
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            result = self._command("logbook/get_events", start_time=start, entity_ids=[entity_id])
        except Exception as exc:  # noqa: BLE001 - history is best-effort
            logger.info("Logbook unavailable for %s (%s)", entity_id, exc)
            return []
        return result if isinstance(result, list) else []

    def get_media_image(self, entity_picture: str) -> bytes | None:
        """Fetch media artwork bytes for a media_player's ``entity_picture``.

        ``entity_picture`` is usually a signed, relative HA proxy URL; it's
        resolved against the HTTP origin derived from the websocket URL. Returns
        None on any error (artwork is best-effort).
        """
        if not entity_picture:
            return None
        url = entity_picture
        if url.startswith("/"):
            base = self._url
            for ws, http in (("wss://", "https://"), ("ws://", "http://")):
                if base.startswith(ws):
                    base = http + base[len(ws):]
                    break
            if base.endswith("/api/websocket"):
                base = base[: -len("/api/websocket")]
            url = base + entity_picture
        try:
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._token}"})
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - HA origin
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - artwork is best-effort
            logger.info("Media image fetch failed (%s)", exc)
            return None

    def call_service(self, domain: str, service: str, entity_id: str, data: dict | None = None) -> None:
        self._execute(
            lambda: self._cmd.trigger_service(domain, service, entity_id=entity_id, **(data or {}))  # type: ignore[union-attr]
        )

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
