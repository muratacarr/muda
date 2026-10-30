"""In-memory sliding-window rate limiter (single process; resets on restart by design)."""

import time
from collections import deque
from collections.abc import Callable


class SlidingWindowLimiter:
    def __init__(
        self, max_events: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_events
        self._window = window_s
        self._clock = clock
        self._events: deque[float] = deque()

    def allow(self) -> bool:
        """Record the event and return True if under the limit. Rejected events are not counted."""
        now = self._clock()
        while self._events and now - self._events[0] >= self._window:
            self._events.popleft()
        if len(self._events) >= self._max:
            return False
        self._events.append(now)
        return True
