"""
Adaptive backoff: sliding-window block rate + global cooldown when block rate is high.
Per-request exponential retry is applied in the crawler; this module handles global slowdown.
"""

import threading
import time
from collections import deque


class AdaptiveBackoff:
    """
    Tracks recent requests and blocks in a sliding window.
    When block rate exceeds threshold, cooldown_seconds() returns a positive value.
    """

    def __init__(
        self,
        window_size: int = 100,
        block_rate_threshold: float = 0.15,
        cooldown_seconds: float = 45.0,
        cooldown_duration_seconds: float = 60.0,
    ) -> None:
        self._window_size = window_size
        self._threshold = block_rate_threshold
        self._cooldown_seconds = cooldown_seconds
        self._cooldown_duration = cooldown_duration_seconds
        self._recent: deque[bool] = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._cooldown_until: float = 0.0

    def notify(self, was_block: bool) -> None:
        """Record one request outcome (block or success)."""
        with self._lock:
            self._recent.append(was_block)

    def cooldown_seconds(self) -> float:
        """
        If we should slow down, return seconds to sleep (e.g. 45).
        Caller should sleep this long then continue. Returns 0 if no cooldown.
        """
        with self._lock:
            now = time.monotonic()
            if now < self._cooldown_until:
                return max(0, self._cooldown_until - now)

            if len(self._recent) < self._window_size:
                return 0.0

            blocks = sum(1 for b in self._recent if b)
            rate = blocks / len(self._recent)
            if rate >= self._threshold:
                self._cooldown_until = now + self._cooldown_duration
                return self._cooldown_seconds
            return 0.0

    def wait_if_cooldown(self) -> None:
        """Convenience: sleep if in cooldown."""
        secs = self.cooldown_seconds()
        if secs > 0:
            time.sleep(secs)
