"""ratelimiter -- small, dependency-free rate-limiting primitives.

Two strategies are provided, both of which keep all of their state in the
instance and never touch the network or the filesystem:

- :class:`TokenBucket` -- a classic token bucket. A bucket holds up to
  ``capacity`` tokens and refills at ``refill_rate`` tokens per second. A call
  may proceed only if enough tokens are available; otherwise it can wait.
- :class:`SlidingWindowCounter` -- counts requests in a trailing time window
  and rejects a call once the window is full. Smoother than a fixed window
  because it never lets a burst straddle a boundary.

Time is read through an injectable ``clock`` (defaulting to
:func:`time.monotonic`) and waiting goes through an injectable ``sleep``
(defaulting to :func:`time.sleep`). Injecting both lets you drive the limiter
from a fake clock in tests, so behaviour is deterministic and no test ever has
to sleep in real time.

Every numeric argument -- ``capacity``, ``refill_rate``, ``window``, ``limit``
and the per-call ``tokens`` -- must be **finite** and positive. ``nan`` and
``inf`` are rejected with :class:`ValueError`. A non-finite parameter would
otherwise build a limiter that silently never admits (``capacity=nan``) or
never expires (``window=inf``), which is far harder to debug than an immediate
error.

Concurrency
-----------
These limiters are **not thread-safe**. Each keeps mutable state -- the token
count and ``_last`` timestamp, or the ``_events`` deque -- that a single thread
mutates without locking, so sharing one instance across threads can over-admit.
Either give each thread its own instance or serialise the calls behind your own
lock. The primitives are deliberately lock-free so a caller that does not need
thread safety pays nothing for it.

Public API
----------
- :class:`RateLimitExceeded`
- :class:`TokenBucket`
- :class:`SlidingWindowCounter`

Run the demo and the tests with::

    cd python/rate-limiter
    python example.py
    python -m pytest
"""

from __future__ import annotations

import math
import time
from collections import deque
from typing import Callable, Optional

__all__ = [
    "RateLimitExceeded",
    "TokenBucket",
    "SlidingWindowCounter",
]


class RateLimitExceeded(Exception):
    """Raised when a blocking :meth:`TokenBucket.acquire` cannot proceed in time."""


class TokenBucket:
    """A token bucket rate limiter.

    The bucket starts full (``capacity`` tokens). Each :meth:`try_acquire` or
    :meth:`acquire` removes ``tokens`` tokens if they are available. Tokens
    accumulate over time at ``refill_rate`` per second, capped at ``capacity``.

    Parameters
    ----------
    capacity:
        Maximum number of tokens the bucket can hold (finite, ``> 0``).
    refill_rate:
        Tokens added per second (finite, ``> 0``).
    clock:
        Callable returning the current time in seconds. Defaults to
        :func:`time.monotonic`.
    sleep:
        Callable used by :meth:`acquire` to wait, taking a number of seconds.
        Defaults to :func:`time.sleep`.
    start_full:
        When ``True`` (the default) the bucket begins full; when ``False`` it
        begins empty.
    """

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
        start_full: bool = True,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if not math.isfinite(capacity):
            raise ValueError("capacity must be a finite number")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be > 0")
        if not math.isfinite(refill_rate):
            raise ValueError("refill_rate must be a finite number")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._tokens = self.capacity if start_full else 0.0
        self._last = self._clock()

    # -- internals ---------------------------------------------------------

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = now
        # If the clock moved backwards (elapsed <= 0) we leave ``_last`` where
        # it is: a later jump back to the same wall time therefore grants no
        # phantom tokens, and the limiter never over-issues.

    @staticmethod
    def _validate_tokens(tokens: float, capacity: float) -> None:
        """Reject token requests that are non-finite, non-positive or too large."""
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        if tokens > capacity:
            raise ValueError("cannot acquire more tokens than the bucket capacity")
        if not math.isfinite(tokens):
            # ``nan`` slips past both comparisons above (every comparison with
            # nan is False), so it must be caught explicitly.
            raise ValueError("tokens must be a finite number")

    # -- public API --------------------------------------------------------

    @property
    def tokens(self) -> float:
        """The number of tokens currently available (after refilling)."""
        self._refill()
        return self._tokens

    def try_acquire(self, tokens: float = 1) -> bool:
        """Take ``tokens`` from the bucket if available.

        Returns ``True`` and consumes the tokens on success, or ``False`` and
        changes nothing when the bucket is short.
        """
        self._validate_tokens(tokens, self.capacity)
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def time_until_available(self, tokens: float = 1) -> float:
        """Seconds until ``tokens`` could be acquired (``0.0`` if available now)."""
        self._validate_tokens(tokens, self.capacity)
        self._refill()
        deficit = tokens - self._tokens
        if deficit <= 0:
            return 0.0
        return deficit / self.refill_rate

    def acquire(self, tokens: float = 1, timeout: Optional[float] = None) -> float:
        """Block until ``tokens`` are available and consume them.

        Returns the number of seconds waited. Raises :class:`RateLimitExceeded`
        if ``timeout`` is set and the wait would exceed it. ``timeout=None``
        (the default) waits indefinitely.
        """
        if tokens > self.capacity:
            raise ValueError("cannot acquire more tokens than the bucket capacity")
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be >= 0 or None")

        start = self._clock()
        deadline = None if timeout is None else start + timeout
        while True:
            if self.try_acquire(tokens):
                return self._clock() - start
            wait = self.time_until_available(tokens)
            if deadline is not None and self._clock() + wait > deadline:
                raise RateLimitExceeded(
                    f"would need to wait {wait:.3f}s, exceeding timeout {timeout}s"
                )
            self._sleep(wait)


class SlidingWindowCounter:
    """A sliding-window rate limiter.

    Keeps the timestamps of recent admissions and allows a call only if the
    number of admissions within the trailing ``window`` seconds would stay at
    or below ``limit``. A call may request more than one slot with ``tokens``.

    State is one timestamp per admitted request, so memory use is ``O(limit)``
    regardless of how much time passes.

    Parameters
    ----------
    limit:
        Maximum number of admissions permitted per window (finite, ``> 0``).
    window:
        Window length in seconds (finite, ``> 0``).
    clock:
        Callable returning the current time in seconds. Defaults to
        :func:`time.monotonic`.
    """

    def __init__(
        self,
        limit: int,
        window: float,
        *,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be > 0")
        if not math.isfinite(limit):
            raise ValueError("limit must be a finite number")
        if window <= 0:
            raise ValueError("window must be > 0")
        if not math.isfinite(window):
            raise ValueError("window must be a finite number")
        self.limit = int(limit)
        self.window = float(window)
        self._clock = clock or time.monotonic
        self._events: "deque[float]" = deque()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        events = self._events
        while events and events[0] <= cutoff:
            events.popleft()

    def allow(self, tokens: int = 1) -> bool:
        """Admit ``tokens`` requests if the window has room, else refuse.

        Returns ``True`` and records the admission on success, or ``False`` and
        records nothing when the window is full.
        """
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        if tokens > self.limit:
            raise ValueError("cannot admit more tokens than the limit")
        if not math.isfinite(tokens):
            # ``nan`` slips past both comparisons above, so catch it explicitly.
            raise ValueError("tokens must be a finite number")
        now = self._clock()
        self._prune(now)
        if len(self._events) + tokens <= self.limit:
            for _ in range(tokens):
                self._events.append(now)
            return True
        return False

    def remaining(self) -> int:
        """How many admissions are still available in the current window."""
        self._prune(self._clock())
        return self.limit - len(self._events)

    def reset(self) -> None:
        """Forget all recorded admissions."""
        self._events.clear()
