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

Validation
----------
Every numeric argument -- ``capacity``, ``refill_rate``, ``window``, ``limit``,
``timeout`` and the per-call ``tokens`` -- must be **finite**. ``nan`` and
``inf`` are rejected with :class:`ValueError`. A non-finite parameter would
otherwise build a limiter that silently never admits (``capacity=nan``) or
never expires (``window=inf``), which is far harder to debug than an immediate
error. A ``nan`` timeout is the worst of the set: every comparison against it
is false, so a naive ``timeout < 0`` check lets it through and the call blocks
forever instead of timing out.

Counts -- ``limit`` and the integer ``tokens`` arguments -- must additionally
be **whole numbers**. A non-integral count is rejected rather than truncated:
``limit=2.9`` silently becoming 2 would halve a budget with no error, and a
fractional ``tokens`` cannot be admitted in the first place.

Every rejection is a :class:`ValueError`, never a ``TypeError`` leaking out of
an internal comparison, so callers can validate untrusted configuration with a
single ``except ValueError``. Text values (``str``/``bytes``) are rejected
rather than coerced: reading ``LIMIT=100`` from the environment is the caller's
job, and implicit coercion hides the mistake until traffic arrives.

Window semantics
----------------
:class:`SlidingWindowCounter` uses a half-open window ``(now - window, now]``:
an admission exactly ``window`` seconds old has expired and frees its slot.
:meth:`SlidingWindowCounter.retry_after` reports the exact instant at which
that happens.

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
from itertools import repeat
from typing import Callable, Optional

__all__ = [
    "RateLimitExceeded",
    "TokenBucket",
    "SlidingWindowCounter",
]


def _require_number(name: str, value: float) -> float:
    """Return ``value`` as a finite, positive ``float``.

    Raises :class:`ValueError` for text input, non-numeric input, ``nan``,
    ``inf`` and non-positive values. Centralising the checks keeps the error
    wording identical across both limiters and guarantees no ``TypeError``
    escapes from a bare comparison against caller-supplied data.
    """
    if isinstance(value, (str, bytes, bytearray)):
        # ``float("100")`` would succeed here; refusing text keeps the trust
        # boundary explicit and pushes env/config parsing to the caller.
        raise ValueError(f"{name} must be a number, not {type(value).__name__}")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a number, got {type(value).__name__}"
        ) from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number, got {number!r}")
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {number!r}")
    return number


def _require_whole_number(name: str, value: float) -> int:
    """Return ``value`` as a positive ``int``, rejecting fractional counts."""
    number = _require_number(name, value)
    if number != int(number):
        raise ValueError(f"{name} must be a whole number, got {number!r}")
    return int(number)


def _validate_request(tokens: float, upper: float, too_large: str) -> None:
    """Validate a per-call token count against ``upper``."""
    _require_number("tokens", tokens)
    if tokens > upper:
        raise ValueError(too_large)


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
        self.capacity = _require_number("capacity", capacity)
        self.refill_rate = _require_number("refill_rate", refill_rate)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._tokens = self.capacity if start_full else 0.0
        self._last = self._clock()

    # -- internals ---------------------------------------------------------

    def _refill(self) -> None:
        """Credit tokens for the time elapsed since the last refill."""
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
        _validate_request(
            tokens,
            capacity,
            "cannot acquire more tokens than the bucket capacity",
        )

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
        (the default) waits indefinitely; ``timeout`` must otherwise be a
        finite number ``>= 0`` (use ``None``, not ``inf``, to wait forever).

        Arguments are validated up front, so a bad ``tokens`` or ``timeout``
        raises before anything is consumed or any time is spent waiting.
        """
        # Validate through the shared helper rather than a bare
        # ``tokens > self.capacity`` comparison: that comparison lets ``nan``
        # through (every comparison with nan is false) and raises ``TypeError``
        # for non-numeric input.
        self._validate_tokens(tokens, self.capacity)
        if timeout is not None:
            if isinstance(timeout, (str, bytes, bytearray)):
                raise ValueError("timeout must be a number or None")
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                raise ValueError(
                    f"timeout must be a number or None, got {type(timeout).__name__}"
                ) from None
            if not math.isfinite(timeout):
                raise ValueError(
                    f"timeout must be a finite number or None, got {timeout!r}"
                )
            if timeout < 0:
                raise ValueError(f"timeout must be >= 0 or None, got {timeout!r}")

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

    The window is half-open, ``(now - window, now]``: an admission exactly
    ``window`` seconds old has expired and frees its slot.

    State is one timestamp per admitted request, so memory use is ``O(limit)``
    regardless of how much time passes. Pruning is amortised ``O(1)`` per
    admission -- each timestamp is appended once and popped once -- but a
    single :meth:`allow` call may pop up to ``limit`` expired entries, so the
    worst-case cost of one call is ``O(limit)``.

    Parameters
    ----------
    limit:
        Maximum number of admissions permitted per window (whole number,
        finite, ``> 0``). A fractional limit is rejected rather than truncated.
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
        self.limit = _require_whole_number("limit", limit)
        self.window = _require_number("window", window)
        self._clock = clock or time.monotonic
        self._events: "deque[float]" = deque()

    # -- internals ---------------------------------------------------------

    def _prune(self, now: float) -> None:
        """Drop admissions that have aged out of the window."""
        cutoff = now - self.window
        events = self._events
        while events and events[0] <= cutoff:
            events.popleft()

    # -- public API --------------------------------------------------------

    def allow(self, tokens: int = 1) -> bool:
        """Admit ``tokens`` requests if the window has room, else refuse.

        Returns ``True`` and records the admission on success, or ``False`` and
        records nothing when the window is full.
        """
        # ``_require_whole_number`` matters here: without it a fractional
        # ``tokens`` reached ``range(tokens)`` and blew up with a ``TypeError``
        # *after* the state had been pruned, which is both a crash on
        # untrusted input and a contract violation (bad arguments are
        # documented to raise ``ValueError``).
        tokens = _require_whole_number("tokens", tokens)
        if tokens > self.limit:
            raise ValueError("cannot admit more tokens than the limit")
        now = self._clock()
        self._prune(now)
        if len(self._events) + tokens <= self.limit:
            # ``repeat`` avoids materialising a temporary list for a burst.
            self._events.extend(repeat(now, tokens))
            return True
        return False

    def retry_after(self, tokens: int = 1) -> float:
        """Seconds until ``tokens`` admissions would fit in the window.

        Returns ``0.0`` when the window has room right now. This is the
        counterpart of :meth:`TokenBucket.time_until_available` and is what a
        web handler needs to populate a ``Retry-After`` header without
        reimplementing the window arithmetic.

        The result is exact for the half-open window: sleeping for it and then
        calling :meth:`allow` with the same ``tokens`` succeeds.
        """
        tokens = _require_whole_number("tokens", tokens)
        if tokens > self.limit:
            raise ValueError("cannot admit more tokens than the limit")
        now = self._clock()
        self._prune(now)
        deficit = len(self._events) + tokens - self.limit
        if deficit <= 0:
            return 0.0
        # ``deficit`` slots must free up, so the ``deficit``-th oldest
        # admission has to age out; it expires exactly ``window`` seconds
        # after it was recorded.
        return max(0.0, self._events[deficit - 1] + self.window - now)

    def remaining(self) -> int:
        """How many admissions are still available in the current window."""
        self._prune(self._clock())
        return self.limit - len(self._events)

    def reset(self) -> None:
        """Forget all recorded admissions."""
        self._events.clear()
