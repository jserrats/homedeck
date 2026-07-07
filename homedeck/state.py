"""Best-effort persistence for runtime settings (e.g. display rotation).

Stored as JSON at ``HOMEDECK_STATE_FILE`` (default ``~/.homedeck/state.json``).
All operations swallow errors — a read-only filesystem just means settings
don't persist across restarts, which is not fatal.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


def _path() -> str:
    return os.environ.get("HOMEDECK_STATE_FILE") or os.path.expanduser("~/.homedeck/state.json")


def load() -> dict:
    try:
        with open(_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001 - corrupt/unreadable state is non-fatal
        logger.info("Could not read state file (%s)", exc)
        return {}


def save(data: dict) -> None:
    path = _path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump(data, fh)
    except Exception as exc:  # noqa: BLE001 - can't persist -> keep going in-memory
        logger.info("Could not write state file (%s)", exc)
