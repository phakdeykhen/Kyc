"""Per-key request rate limit: a fixed one-minute window held in this process.

Each API process counts on its own, so N replicas allow up to N times the limit. A shared
counter (Redis, already reserved as REDIS_URL) replaces this in the deployment phases.
"""

from dataclasses import dataclass
import math
import threading
import time


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


class RateLimiter:
    WINDOW = 60.0

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def hit(self, key: str, limit: int) -> RateDecision:
        current = self._clock()
        with self._lock:
            start, count = self._windows.get(key, (current, 0))
            if current - start >= self.WINDOW:
                start, count = current, 0
            if len(self._windows) > 10_000:   # forget idle keys
                self._windows = {k: v for k, v in self._windows.items() if current - v[0] < self.WINDOW}
            if count >= limit:
                return RateDecision(False, limit, 0, max(1, math.ceil(self.WINDOW - (current - start))))
            self._windows[key] = (start, count + 1)
            return RateDecision(True, limit, limit - count - 1, 0)
