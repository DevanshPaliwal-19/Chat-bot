"""In-memory sliding-window rate limiter (single-process; use a shared store if you scale out)."""
from __future__ import annotations

import time
from collections import deque
from threading import Lock


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_s: float) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque[float]] = {}
        self._lock = Lock()

    def check(self, key: str, now: float | None = None) -> tuple[bool, float]:
        """Record an event. Returns (allowed, retry_after_seconds)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._events) > 20000:
                self._evict(now)
            q = self._events.setdefault(key, deque())
            while q and now - q[0] >= self.window_s:
                q.popleft()
            if len(q) >= self.max_events:
                return False, max(0.0, self.window_s - (now - q[0]))
            q.append(now)
            return True, 0.0

    def _evict(self, now: float) -> None:
        for k in [k for k, q in self._events.items() if not q or now - q[-1] >= self.window_s]:
            del self._events[k]
