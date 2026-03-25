"""Sliding-window per-IP rate limiter (in-memory, no Redis required)."""

import asyncio
import math
import time
from collections import deque


class RateLimiter:
    """Sliding-window rate limiter keyed by IP address.

    Stores a deque of request timestamps per IP. On each check, timestamps
    outside the window are evicted before counting.
    """

    def __init__(self) -> None:
        self._windows: dict[str, deque[float]] = {}
        self._lock = asyncio.Lock()

    async def check(
        self, ip: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Check whether the IP is within the rate limit.

        Returns:
            (allowed, retry_after_seconds)
            - allowed=True, retry_after=0 if the request is permitted
            - allowed=False, retry_after>0 if the limit is exceeded
        """
        now = time.monotonic()
        window_start = now - window_seconds

        async with self._lock:
            if ip not in self._windows:
                self._windows[ip] = deque()

            dq = self._windows[ip]

            # Evict timestamps outside the sliding window
            while dq and dq[0] < window_start:
                dq.popleft()

            if len(dq) >= limit:
                # Oldest request in window determines when a slot opens
                oldest = dq[0]
                retry_after = math.ceil(oldest - window_start)
                return False, max(retry_after, 1)

            dq.append(now)
            return True, 0
