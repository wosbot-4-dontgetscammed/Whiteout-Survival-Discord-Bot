"""Shared request queues that protect the upstream APIs from this bot.

Every part of the bot talks to a small number of third-party services, and each
of them can punish bursts: the game API answers `40019 TOO FREQUENT`, WoS Atlas
returns `429` once its 120-requests-per-minute budget is spent. Before this
module each cog paced itself in isolation, so two features running at the same
time could double or triple the real request rate without either noticing.

`RequestQueue` fixes that by being the single gate per upstream host:

* one queue per host, shared by every caller in the process
* a token bucket (sustained `rate` per second, short `burst` allowance)
* strictly ordered - callers are served first-come-first-served
* `pause(seconds)` lets a caller that just saw a 429/40019 hold the whole queue
  back, so a rate limit slows every feature instead of only the unlucky one

Usage::

    async with atlas_queue().slot():
        ...  # perform exactly one request
"""

import asyncio
import contextlib
import time

from .log_config import get_logger

logger = get_logger("api_queue")


class RequestQueue:
    """Serialised, rate-limited access to one upstream host."""

    def __init__(self, name: str, rate: float, burst: int = 1):
        self.name = name
        self.rate = rate                  # sustained requests per second
        self.burst = max(1, burst)        # tokens available after an idle spell
        self._tokens = float(self.burst)
        self._updated = time.monotonic()
        self._paused_until = 0.0
        self._lock = asyncio.Lock()
        self.served = 0
        self.waited = 0.0

    def _refill(self):
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    def pause(self, seconds: float, reason: str = ""):
        """Hold every caller back - used after a rate-limit response."""
        until = time.monotonic() + max(0.0, seconds)
        if until > self._paused_until:
            self._paused_until = until
            logger.info(
                "%s queue paused for %.1fs%s", self.name, seconds, f" ({reason})" if reason else ""
            )

    @contextlib.asynccontextmanager
    async def slot(self):
        """Wait for permission to send exactly one request."""
        async with self._lock:
            start = time.monotonic()
            while True:
                now = time.monotonic()
                if now < self._paused_until:
                    await asyncio.sleep(self._paused_until - now)
                    continue
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    break
                await asyncio.sleep((1.0 - self._tokens) / self.rate)
            self.served += 1
            self.waited += time.monotonic() - start
            try:
                yield
            finally:
                pass

    def stats(self) -> dict:
        return {
            "name": self.name,
            "served": self.served,
            "total_wait_s": round(self.waited, 1),
            "rate_per_s": self.rate,
            "paused": max(0.0, round(self._paused_until - time.monotonic(), 1)),
        }


_queues: dict[str, RequestQueue] = {}


def get_queue(name: str, rate: float, burst: int = 1) -> RequestQueue:
    """Return the process-wide queue for `name`, creating it on first use."""
    queue = _queues.get(name)
    if queue is None:
        queue = RequestQueue(name, rate, burst)
        _queues[name] = queue
    return queue


def atlas_queue() -> RequestQueue:
    """WoS Atlas: 120 requests per 60s. Kept well under it (~0.9/s)."""
    return get_queue("wosatlas", rate=0.9, burst=2)


def game_queue() -> RequestQueue:
    """CenturyGame gift API: no published global limit, but it throttles per
    FID (40019) and sits behind a WAF, so bursts are what get us blocked."""
    return get_queue("centurygame", rate=3.0, burst=5)


def all_stats() -> list[dict]:
    return [q.stats() for q in _queues.values()]
