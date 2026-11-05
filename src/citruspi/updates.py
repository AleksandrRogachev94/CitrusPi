"""A bounded handoff from producers to the optional background publisher."""

from __future__ import annotations

import threading
from typing import Any


class Updates:
    """Coalesce pending updates by kind; producers never perform network work.

    This carries current state, not an event history. A slow/disconnected consumer
    cannot build an unbounded backlog. Taking updates and clearing the wake signal
    share a lock so a concurrent producer's signal cannot be lost.
    """

    def __init__(self) -> None:
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._pending: dict[str, Any] = {}

    def notify(self, kind: str, payload: Any = None) -> None:
        with self._lock:
            self._pending[kind] = payload
            self.ready.set()

    def take(self) -> dict[str, Any]:
        with self._lock:
            pending = self._pending
            self._pending = {}
            self.ready.clear()
            return pending
