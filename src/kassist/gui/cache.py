"""Small in-process cache for the GUI: values expire after a time and are dropped on every change kassist
makes itself (any POST), so edits show at once and changes made directly in the folder within minutes."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.clock = clock

    def get(self, key: str, factory: Callable[[], Any], ttl: float) -> Any:
        now = self.clock()
        with self._lock:
            hit = self._data.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = factory()
        with self._lock:
            self._data[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
