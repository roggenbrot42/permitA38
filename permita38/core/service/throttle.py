"""Failed-attempt throttling and request rate limiting.

A caller supplies one or more *keys* per attempt: the username, ``totp:<user id>``, ``pw:<user id>`` and, since the
sessions work, ``ip:<client address>`` (IPv6 addresses are bucketed by /64). An attempt is refused as soon as ANY of its
keys has reached the limit, and a failure is counted against ALL of its keys.

Two flavours of :class:`AttemptThrottle`:

* plain sliding window (account keys): after ``max_failures`` failures in ``window`` seconds the key is locked until the
  oldest failures age out. Deliberately no permanent or ever-growing lock-out, so an attacker cannot lock a legitimate
  user out for good.
* ``backoff=True`` (address keys): the first lock-out lasts until the window frees up; every further burst that hits the
  limit again doubles the minimum lock-out (capped at ``max_lock`` seconds). Strikes are forgotten after an hour without
  failures.

Refusals carry a ``Retry-After`` header. State is in memory and per process (a restart resets it; multiple worker
processes do not share it).
"""

import math
import threading
import time

from permita38.core.errors import ServiceError

MAX_FAILURES = 5
FAILURE_WINDOW = 300  # seconds
STRIKE_MEMORY = 3600  # seconds without failures after which the backoff level resets


def too_many(retry_after: float, message: str = "Too many failed attempts. Please wait a few minutes and try again."):
    seconds = max(1, math.ceil(retry_after))
    return ServiceError(429, message, headers={"Retry-After": str(seconds)})


class AttemptThrottle:
    def __init__(
        self,
        max_failures: int = MAX_FAILURES,
        window: float = FAILURE_WINDOW,
        backoff: bool = False,
        max_lock: float = 3600,
    ):
        self.max_failures = max_failures
        self.window = window
        self.backoff = backoff
        self.max_lock = max_lock
        self._failures: dict[str, list[float]] = {}
        self._locks: dict[str, float] = {}  # key -> locked until (backoff mode)
        self._strikes: dict[str, tuple[int, float]] = {}  # key -> (level, last strike time)
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < self.window]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def _retry_after(self, key: str, now: float) -> float:
        """Seconds until ``key`` may try again (0 = now)."""
        recent = self._recent(key, now)
        wait = 0.0
        if len(recent) >= self.max_failures:
            # enough of the oldest failures must age out to get below the limit
            wait = recent[len(recent) - self.max_failures] + self.window - now
        locked = self._locks.get(key, 0.0) - now
        if locked > 0:
            wait = max(wait, locked)
        elif key in self._locks:
            del self._locks[key]
        return max(wait, 0.0)

    def check(self, *keys: str) -> None:
        """Raise 429 (with Retry-After) if any key is currently locked out."""
        now = time.time()
        with self._lock:
            wait = max((self._retry_after(k, now) for k in keys), default=0.0)
        if wait > 0:
            raise too_many(wait)

    def fail(self, *keys: str) -> None:
        now = time.time()
        with self._lock:
            for k in keys:
                before = len(self._recent(k, now))
                self._failures.setdefault(k, []).append(now)
                if self.backoff and before + 1 >= self.max_failures and before < self.max_failures:
                    self._strike(k, now)

    def _strike(self, key: str, now: float) -> None:
        level, last = self._strikes.get(key, (0, 0.0))
        if now - last > STRIKE_MEMORY:
            level = 0
        level += 1
        self._strikes[key] = (level, now)
        if level > 1:  # the first burst only waits for the window; repeated bursts double the lock
            self._locks[key] = now + min(self.window * 2 ** (level - 1), self.max_lock)

    def clear(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._failures.pop(k, None)
                self._locks.pop(k, None)


class RateLimiter:
    """Sliding-window request counter per key (``limit`` requests per ``window`` seconds)."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def hit(self, key: str) -> None:
        """Count one request; raise 429 with Retry-After when the key is over its limit."""
        now = time.time()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                raise too_many(hits[0] + self.window - now, "Too many requests. Please slow down.")
            hits.append(now)
            self._hits[key] = hits
            if now - self._last_sweep > self.window:  # forget idle keys so the dict cannot grow without bound
                self._last_sweep = now
                self._hits = {k: v for k, v in self._hits.items() if v and now - v[-1] < self.window}
