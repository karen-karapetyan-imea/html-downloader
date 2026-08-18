"""
Smooth global rate limiter with human-like jitter (0.5–2s).
Thread-safe; shared across all workers to avoid burst patterns.
"""

import random
import threading
import time


class RateLimiter:
    """Thread-safe rate limiter: min interval between requests + optional jitter."""

    def __init__(
        self,
        requests_per_second: float,
        jitter_min: float = 0.5,
        jitter_max: float = 2.0,
    ) -> None:
        self._rps = max(0.1, requests_per_second)
        self._min_interval = 1.0 / self._rps
        self._jitter_min = jitter_min
        self._jitter_max = jitter_max
        self._last_request_time = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Wait to maintain rate limit, then apply random human-like delay."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

        jitter = random.uniform(self._jitter_min, self._jitter_max)
        time.sleep(jitter)
