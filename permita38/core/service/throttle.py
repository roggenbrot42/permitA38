"""Failed-attempt throttling.

A caller supplies one or more *keys* per attempt (the username, ``totp:<user id>``, or ``ip:<address>`` for invalid API tokens): an attempt is refused as soon as ANY of its
keys has reached the limit, and a failure is counted against ALL of its keys. State is in memory and per process.
"""

import math
import threading
import time

from permita38.core.errors import ServiceError

MAX_FAILURES = 5
FAILURE_WINDOW = 300  # seconds


class AttemptThrottle:
    def __init__(self, max_failures: int = MAX_FAILURES, window: float = FAILURE_WINDOW):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def check(self, *keys: str) -> None:
        """Raise 429 if any key is currently locked out."""
        now = time.time()
        with self._lock:
            waits = []
            for k in keys:
                recent = self._recent(k, now)
                if len(recent) >= self.max_failures:
                    # locked until enough old failures have left the window to get below the limit
                    waits.append(recent[len(recent) - self.max_failures] + self.window - now)
            if waits:
                retry_after = max(1, math.ceil(max(waits)))
                raise ServiceError(
                    429,
                    "Too many failed attempts. Please wait a few minutes and try again.",
                    headers={"Retry-After": str(retry_after)},
                )

    def fail(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for k in keys:
                self._recent(k, now)
                self._failures.setdefault(k, []).append(now)

    def clear(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)
