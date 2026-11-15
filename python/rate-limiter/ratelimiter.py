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

Numeric types other than ``float``/``int`` -- ``Decimal``, ``Fraction``, a
``numpy`` scalar, anything with a working ``__float__`` -- are accepted and
converted **once**, at the boundary, and only the converted ``float`` is used
from then on. Mixing the caller's object into float arithmetic is what breaks
the ``ValueError``-only contract: ``Decimal("1")`` compares fine against a
``float`` and then raises ``TypeError`` on ``1.0 - Decimal("1")``.

``bool`` is refused too, even though Python treats it as an ``int``: a
``limit`` accidentally bound to a feature flag would otherwise quietly mean
``1``. Integers too large to represent as a ``float`` (``10 ** 400``) are
reported as a non-finite :class:`ValueError` rather than letting
:class:`OverflowError` escape, so the single-``except`` contract holds for
every input rather than only for the tidy ones.

Blocking and clock progress
---------------------------
:meth:`TokenBucket.acquire` waits by calling ``sleep`` and then re-reading
``clock``. If the injected clock never advances -- a frozen fake clock, or a
real clock whose resolution is coarser than the wait -- the loop can never
make progress, so after :data:`_MAX_STALLED_WAITS` consecutive sleeps that
leave the clock unchanged it raises :class:`RuntimeError` rather than spinning
forever. This is a caller bug in the injected clock, not bad request data,
hence the different exception type.

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

# Consecutive ``acquire`` sleeps after which a clock that has not moved at all
# is treated as broken rather than merely coarse. Five leaves room for a clock
# with sub-second resolution while still failing in milliseconds on a frozen
# fake clock. See ``TokenBucket.acquire``.
_MAX_STALLED_WAITS = 5


def _as_finite_float(name: str, value: float) -> float:
    """Coerce ``value`` to a finite ``float`` or raise :class:`ValueError`.

    The single place where caller-supplied numbers cross the trust boundary,
    so the rejection wording is identical for every parameter of both
    limiters. Refuses:

    - text (``str``/``bytes``/``bytearray``), rather than coercing it;
    - ``bool``, which is an ``int`` subclass and would otherwise pass as 1/0;
    - anything ``float()`` rejects with ``TypeError``/``ValueError``;
    - integers too large for a ``float`` (``OverflowError``), reported as
      non-finite so that no exception other than ``ValueError`` escapes;
    - ``nan`` and ``inf``.
    """
    if isinstance(value, (str, bytes, bytearray)):
        # ``float("100")`` would succeed here; refusing text keeps the trust
        # boundary explicit and pushes env/config parsing to the caller.
        raise ValueError(f"{name} must be a number, not {type(value).__name__}")
    if isinstance(value, bool):
        # ``bool`` is an ``int`` subclass, so ``float(True)`` is ``1.0`` and a
        # ``limit`` bound to a feature flag would silently become 1.
        raise ValueError(f"{name} must be a number, not bool")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(
            f"{name} must be a number, got {type(value).__name__}"
        ) from None
    except OverflowError:
        # ``float(10 ** 400)`` overflows. An integer too large for a float is
        # not finite as far as a limiter is concerned, and letting
        # ``OverflowError`` through would break the documented promise that
        # every rejection is a ``ValueError``.
        raise ValueError(f"{name} must be a finite number, got {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number, got {number!r}")
    return number


def _require_number(name: str, value: float) -> float:
    """Return ``value`` as a finite, positive ``float``.

    Raises :class:`ValueError` for text input, ``bool``, non-numeric input,
    ``nan``, ``inf``, float-overflowing integers and non-positive values.
    Centralising the checks keeps the error wording identical across both
    limiters and guarantees no ``TypeError`` escapes from a bare comparison
    against caller-supplied data.
    """
    number = _as_finite_float(name, value)
    if number <= 0:
        raise ValueError(f"{name} must be > 0, got {number!r}")
    return number


def _require_non_negative_number(name: str, value: float) -> float:
    """Return ``value`` as a finite ``float`` that is ``>= 0``.

    Identical to :func:`_require_number` except that zero is legal, which is
    what a ``timeout`` of ``0`` means ("do not wait at all"). Sharing
    :func:`_as_finite_float` keeps the two ladders from drifting apart -- the
    previous inline copy missed ``bool`` and ``OverflowError``.
    """
    number = _as_finite_float(name, value)
    if number < 0:
        raise ValueError(f"{name} must be >= 0, got {number!r}")
    return number


def _require_whole_number(name: str, value: float) -> int:
    """Return ``value`` as a positive ``int``, rejecting fractional counts."""
    number = _require_number(name, value)
    if number != int(number):
        raise ValueError(f"{name} must be a whole number, got {number!r}")
    return int(number)


def _validate_request(tokens: float, upper: float, too_large: str) -> float:
    """Validate a per-call token count against ``upper`` and return it as a float.

    The **coerced** value is returned on purpose. Validating ``tokens`` and then
    doing arithmetic on the caller's original object leaks ``TypeError`` out of
    the trust boundary: ``Decimal("1")`` compares happily against a ``float``
    (so the check passes) but ``1.0 - Decimal("1")`` raises. Callers must use
    the returned float for every subsequent comparison and subtraction, which
    keeps the promise that a bad ``tokens`` argument is always a
    :class:`ValueError` and never a ``TypeError`` from inside the limiter.
    """
    number = _require_number("tokens", tokens)
    if number > upper:
        raise ValueError(too_large)
    return number


def _wait_until_expiry(now: float, expiry: float, window: float) -> float:
    """Seconds to wait so that ``(now + wait) - window >= expiry``.

    ``_prune`` frees a slot when ``now - window >= expiry``, so the wait has to
    be measured with that same expression. The naive answer
    ``expiry + window - now`` is a difference of two *rounded* quantities --
    ``expiry + window`` rounds, and ``now + wait`` rounds again -- so it can
    land a fraction of a ULP short of the expiry instant. The caller then
    sleeps, prunes, and finds the admission still inside the window, breaking
    the documented promise that ``sleep(retry_after(n))`` is always enough.

    Padding by a couple of ULPs of the largest magnitude involved (nanoseconds
    for any realistic clock) fixes the common case; the loop re-checks against
    the exact predicate and grows the padding when a clock reading is large
    enough relative to ``window`` that one ULP of the sum is not enough.
    """
    wait = max(0.0, expiry + window - now)
    # ``expiry + window`` and ``now - window`` round differently, so a wait that
    # computes as ``0.0`` or slightly negative can still leave the admission
    # inside the window. Start from the clamped value plus a couple of ULPs and
    # let the loop verify against the predicate ``_prune`` actually uses.
    slack = max(math.ulp(now), math.ulp(expiry + window), math.ulp(window))
    candidate = wait + 2.0 * slack
    for _ in range(4):
        if (now + candidate) - window >= expiry:
            return candidate
        slack *= 16.0
        candidate = wait + 2.0 * slack
    return candidate


class RateLimitExceeded(Exception):
    """Raised when a blocking :meth:`TokenBucket.acquire` cannot proceed in time.

    The message is human-readable, but the same facts are also available as
    numbers so a web handler never has to parse prose to set a header::

        try:
            bucket.acquire(tokens=1, timeout=0.5)
        except RateLimitExceeded as exc:
            headers["Retry-After"] = str(math.ceil(exc.retry_after or 0))
            return 429, headers

    Attributes
    ----------
    retry_after:
        Seconds the call would have needed to wait, or ``None`` when the
        exception was constructed directly.
    requested:
        Number of tokens asked for, or ``None`` when constructed directly.
    """

    def __init__(
        self,
        message: str = "",
        *,
        retry_after: Optional[float] = None,
        requested: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.requested = requested


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
    def _validate_tokens(tokens: float, capacity: float) -> float:
        """Reject token requests that are non-finite, non-positive or too large.

        Returns the request as a ``float``; use that value, not the caller's
        original argument, for the comparisons and subtractions that follow.
        """
        return _validate_request(
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
        tokens = self._validate_tokens(tokens, self.capacity)
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    def time_until_available(self, tokens: float = 1) -> float:
        """Seconds until ``tokens`` could be acquired (``0.0`` if available now)."""
        tokens = self._validate_tokens(tokens, self.capacity)
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

        The exception carries the numeric wait as ``retry_after`` (and the
        token count as ``requested``) for callers that need to set a
        ``Retry-After`` header. If the injected ``clock`` never advances the
        loop cannot progress, so :class:`RuntimeError` is raised after
        :data:`_MAX_STALLED_WAITS` fruitless sleeps rather than spinning.
        """
        # Validate through the shared helper rather than a bare
        # ``tokens > self.capacity`` comparison: that comparison lets ``nan``
        # through (every comparison with nan is false) and raises ``TypeError``
        # for non-numeric input.
        tokens = self._validate_tokens(tokens, self.capacity)
        if timeout is not None:
            # ``None`` means "wait forever"; every other value goes through the
            # same ladder as the rest of the numbers, so ``timeout="1"``,
            # ``timeout=nan``, ``timeout=True`` and ``timeout=10 ** 400`` are
            # all rejected before a token is consumed or a second spent.
            timeout = _require_non_negative_number("timeout", timeout)

        start = self._clock()
        deadline = None if timeout is None else start + timeout
        stalled = 0
        while True:
            if self.try_acquire(tokens):
                return self._clock() - start
            wait = self.time_until_available(tokens)
            if deadline is not None and self._clock() + wait > deadline:
                raise RateLimitExceeded(
                    f"would need to wait {wait:.3f}s, exceeding timeout {timeout}s",
                    retry_after=wait,
                    requested=tokens,
                )
            before = self._clock()
            self._sleep(wait)
            if self._clock() <= before:
                # The clock did not move across the sleep, so the next pass
                # would compute the same ``wait`` and sleep again -- forever on
                # a frozen clock. Give up loudly instead of hanging.
                stalled += 1
                if stalled >= _MAX_STALLED_WAITS:
                    raise RuntimeError(
                        "clock did not advance after "
                        f"{stalled} sleeps; acquire() cannot make progress "
                        "(is the injected clock frozen or too coarse for the "
                        "requested refill rate?)"
                    )
            else:
                stalled = 0


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

        The result satisfies the half-open window exactly: sleeping for it and
        then calling :meth:`allow` with the same ``tokens`` succeeds. Because
        the expiry instant is reached through float arithmetic, the value is
        rounded *up* by at most a few ULPs of the clock reading -- nanoseconds
        on a real clock -- so it is safe to use directly as a
        ``Retry-After`` value without adding your own slack.
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
        # after it was recorded. ``_wait_until_expiry`` rounds *up* to the
        # first wait that actually satisfies ``_prune``, so the result is
        # usable as a ``Retry-After`` value without adding slack by hand.
        return _wait_until_expiry(now, self._events[deficit - 1], self.window)

    def remaining(self) -> int:
        """How many admissions are still available in the current window."""
        self._prune(self._clock())
        return self.limit - len(self._events)

    def reset(self) -> None:
        """Forget all recorded admissions."""
        self._events.clear()
