from __future__ import annotations

from collections import deque
from threading import Lock
from time import monotonic, sleep
from typing import Callable


GRIDSTATUS_MAX_REQUESTS_PER_MINUTE = 30
GRIDSTATUS_SAFE_INTERVAL_SECONDS = 2.1


class GridStatusRequestLimiter:
    """Coordinate all GridStatus HTTP requests made by this process."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = GRIDSTATUS_SAFE_INTERVAL_SECONDS,
        max_requests_per_minute: int = GRIDSTATUS_MAX_REQUESTS_PER_MINUTE,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self.min_interval_seconds = max(
            float(min_interval_seconds),
            GRIDSTATUS_SAFE_INTERVAL_SECONDS,
        )
        self.max_requests_per_minute = max_requests_per_minute
        self.clock = clock
        self.sleeper = sleeper
        self.last_request_at: float | None = None
        self.request_times: deque[float] = deque()
        self.lock = Lock()

    def wait(self, *, min_interval_seconds: float | None = None) -> None:
        safe_interval = max(
            float(min_interval_seconds or self.min_interval_seconds),
            GRIDSTATUS_SAFE_INTERVAL_SECONDS,
        )
        with self.lock:
            while True:
                now = self.clock()
                while self.request_times and now - self.request_times[0] >= 60.0:
                    self.request_times.popleft()

                wait_seconds = 0.0
                if self.last_request_at is not None:
                    wait_seconds = max(
                        wait_seconds,
                        safe_interval - (now - self.last_request_at),
                    )
                if (
                    self.max_requests_per_minute > 0
                    and len(self.request_times) >= self.max_requests_per_minute
                ):
                    wait_seconds = max(
                        wait_seconds,
                        60.0 - (now - self.request_times[0]) + 0.01,
                    )

                if wait_seconds <= 0.001:
                    self.last_request_at = now
                    self.request_times.append(now)
                    return
                self.sleeper(wait_seconds)


gridstatus_request_limiter = GridStatusRequestLimiter()
