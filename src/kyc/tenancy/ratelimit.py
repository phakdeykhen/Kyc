"""Per-credential fixed-window rate limiting.

In-process only: with several API instances each enforces its own window, so the
effective limit is the per-key limit times the instance count. A shared store
(Memorystore) replaces this in the deployment phase.
"""

from collections.abc import Callable
import threading
import time


class RateLimiter:
    def __init__(self, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic, max_keys: int = 50_000):
        self.window = window_seconds
        self.clock = clock
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}

    def hit(self, key: str, limit: int) -> float | None:
        """Count one request; return the seconds to wait when the limit is exceeded."""
        now = self.clock()
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= self.window:
                start, count = now, 0
            if count >= limit:
                return max(self.window - (now - start), 0.001)
            if key not in self._windows and len(self._windows) >= self.max_keys:
                self._windows = {name: value for name, value in self._windows.items() if now - value[0] < self.window}
            self._windows[key] = (start, count + 1)
            return None
