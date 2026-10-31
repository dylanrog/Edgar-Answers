"""Per-IP and site-wide caps on POST /ask.

Every answer is 3-5 paid Haiku calls and the demo has no accounts, so these
caps are what bounds the bill (the Anthropic console spend limit is the
backstop, not the plan). State is in-process: correct only because the deploy
runs one machine with one uvicorn worker (backend/fly.toml). A second process
would keep its own counters and double the effective limits.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping

HOUR = 3600.0
DAY = 86400.0


class RateLimited(Exception):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class AskLimiter:
    def __init__(
        self,
        per_ip_per_hour: int,
        daily_cap: int,
        clock: Callable[[], float] = time.time,
    ):
        self.per_ip_per_hour = per_ip_per_hour
        self.daily_cap = daily_cap
        self._clock = clock
        # /ask is a sync endpoint, so FastAPI runs it on a threadpool.
        self._lock = threading.Lock()
        self._hits: dict[str, deque[float]] = {}
        self._day = -1
        self._day_count = 0

    def check(self, ip: str) -> None:
        """Record one /ask for `ip`, or raise RateLimited without recording it."""
        with self._lock:
            now = self._clock()
            # Epoch seconds are UTC, so this is the UTC day number.
            day = int(now // DAY)
            if day != self._day:
                self._day, self._day_count = day, 0
                # Drop visitors idle for an hour; bounds the dict by a day's traffic.
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - HOUR}
            if self._day_count >= self.daily_cap:
                raise RateLimited(
                    "The demo has reached its question limit for today."
                    " It resets at 00:00 UTC.",
                    retry_after=int((day + 1) * DAY - now) + 1,
                )
            hits = self._hits.setdefault(ip, deque())
            while hits and hits[0] <= now - HOUR:
                hits.popleft()
            if len(hits) >= self.per_ip_per_hour:
                raise RateLimited(
                    f"You've asked {self.per_ip_per_hour} questions in the last hour,"
                    " the limit for this demo. Try again in a little while.",
                    retry_after=int(hits[0] + HOUR - now) + 1,
                )
            hits.append(now)
            self._day_count += 1


def client_ip(headers: Mapping[str, str], peer: str | None, header: str | None) -> str:
    """The visitor's quota bucket. `header` is trusted only when configured: on
    Fly the edge proxy overwrites Fly-Client-IP, so a client cannot forge it
    there. Anywhere else the header is attacker-controlled, so it is ignored."""
    if header:
        value = headers.get(header) or headers.get(header.lower())
        if value:
            return _bucket(value.strip())
    return _bucket(peer) if peer else "unknown"


def _bucket(address: str) -> str:
    """IPv6 visitors are keyed by their /64: one ordinary allocation holds 2**64
    addresses, so per-address keys would let anyone rotate into fresh quota."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if ip.version == 6:
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)
