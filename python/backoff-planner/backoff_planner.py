"""Deadline-aware retry scheduling: capped exponential backoff, jitter, Retry-After.

The interesting part of a retry loop is not the exponential curve -- that is one
line -- it is the bookkeeping around it. A retry must not sleep past the
caller's deadline, must honour a server's ``Retry-After`` without letting a
hostile value stall the process, and must spread retries out so that a fleet of
clients does not come back in lockstep. This module does that bookkeeping and
nothing else: it computes delays, it never sleeps.

    >>> from backoff_planner import RetryPlanner
    >>> plan = RetryPlanner(rng=__import__("random").Random(7)).plan(4)
    >>> len(plan.delays)
    4
    >>> plan.total == sum(plan.delays)
    True
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Tuple, Union

__all__ = [
    "BackoffError",
    "RETRYABLE_STATUS",
    "RetryPlanner",
    "RetryPolicy",
    "Schedule",
    "is_retryable_status",
    "parse_retry_after",
]

#: Status codes where a plain retry is the documented remedy. Notably absent:
#: 501 (not implemented) and 505 (version not supported) -- retrying those
#: repeats the same request against the same incapable server.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

JITTER_MODES = ("none", "full", "equal", "decorrelated")

_DELTA_SECONDS = re.compile(r"[0-9]+\Z")

# float exponents above this overflow a double; the cap applies long before.
_MAX_EXPONENT = 700.0


class BackoffError(ValueError):
    """Raised for a policy, attempt number or ``Retry-After`` that makes no sense."""


def _require_real(value: object, label: str) -> float:
    """Coerce a finite, non-negative number, rejecting bools and NaN/inf."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BackoffError(f"{label} must be a number, not {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise BackoffError(f"{label} must be finite, got {number!r}")
    if number < 0:
        raise BackoffError(f"{label} must not be negative, got {number!r}")
    return number


def _require_attempt(attempt: object) -> int:
    if isinstance(attempt, bool) or not isinstance(attempt, int):
        raise BackoffError(f"attempt must be an int, not {type(attempt).__name__}")
    if attempt < 1:
        raise BackoffError(f"attempt is 1-based, got {attempt}")
    return attempt


@dataclass(frozen=True)
class RetryPolicy:
    """The shape of the backoff curve.

    ``base`` is the delay before the first retry, ``factor`` multiplies it per
    attempt, and ``cap`` bounds it. ``jitter`` decides how much of that delay is
    randomised: ``"none"`` (deterministic), ``"full"`` (uniform in ``0..delay``),
    ``"equal"`` (half fixed, half random) or ``"decorrelated"`` (each delay is
    uniform in ``base..3 * previous``, ignoring ``factor`` -- AWS's variant,
    which survives a thundering herd better than a fixed curve).
    """

    base: float = 0.5
    factor: float = 2.0
    cap: float = 30.0
    jitter: str = "full"

    def __post_init__(self) -> None:
        base = _require_real(self.base, "base")
        factor = _require_real(self.factor, "factor")
        cap = _require_real(self.cap, "cap")
        if factor < 1:
            raise BackoffError(f"factor must be at least 1, got {self.factor!r}")
        if cap < base:
            raise BackoffError(
                f"cap ({cap!r}) is below base ({base!r}); every delay would be capped"
            )
        if self.jitter not in JITTER_MODES:
            raise BackoffError(
                f"jitter must be one of {', '.join(JITTER_MODES)}; got {self.jitter!r}"
            )


@dataclass(frozen=True)
class Schedule:
    """The result of :meth:`RetryPlanner.plan`.

    ``delays`` is the wait before each retry, in order. ``truncated`` is true
    when the deadline cut the schedule short of ``max_attempts``, which is the
    signal to give up rather than retry anyway.
    """

    delays: Tuple[float, ...]
    truncated: bool = False

    def __len__(self) -> int:
        return len(self.delays)

    def __iter__(self):
        return iter(self.delays)

    @property
    def total(self) -> float:
        """Seconds spent waiting across the whole schedule."""
        return math.fsum(self.delays)

    def cumulative(self) -> Tuple[float, ...]:
        """Running total after each delay, handy for a progress log."""
        running = 0.0
        out = []
        for delay in self.delays:
            running += delay
            out.append(running)
        return tuple(out)


class RetryPlanner:
    """Compute retry delays for one policy, optionally from a seeded RNG.

    Pass ``rng=random.Random(seed)`` to make jittered schedules reproducible in
    tests and incident write-ups; leave it out for production, where every
    process should pick its own stream.
    """

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        *,
        rng: Optional[random.Random] = None,
    ) -> None:
        if policy is None:
            policy = RetryPolicy()
        if not isinstance(policy, RetryPolicy):
            raise BackoffError(
                f"policy must be a RetryPolicy, not {type(policy).__name__}"
            )
        if rng is not None and not isinstance(rng, random.Random):
            raise BackoffError(
                f"rng must be a random.Random, not {type(rng).__name__}"
            )
        self.policy = policy
        self._rng = rng

    @property
    def rng(self) -> random.Random:
        """The random source, created on first use when none was supplied."""
        if self._rng is None:
            self._rng = random.Random()
        return self._rng

    # -- the curve ---------------------------------------------------------

    def nominal(self, attempt: int) -> float:
        """The un-jittered delay for ``attempt``: ``base * factor ** (attempt-1)``, capped."""
        attempt = _require_attempt(attempt)
        policy = self.policy
        if attempt == 1 or policy.factor == 1.0:
            raw = float(policy.base)
        else:
            exponent = (attempt - 1) * math.log(policy.factor)
            if exponent > _MAX_EXPONENT:
                raw = float(policy.cap)
            else:
                # Safe to exponentiate directly: the guard above keeps the
                # result inside the double range, and this stays exact for
                # factors like 2.0 where exp() would drift by an ulp.
                raw = policy.base * (policy.factor ** (attempt - 1))
        return min(raw, float(policy.cap))

    def delay_for(self, attempt: int, *, previous: Optional[float] = None) -> float:
        """The delay for ``attempt`` after applying the policy's jitter.

        ``previous`` is the delay actually used last time; only the
        ``"decorrelated"`` mode needs it, and it falls back to ``base`` when
        omitted.
        """
        attempt = _require_attempt(attempt)
        policy = self.policy
        mode = policy.jitter

        if mode == "none":
            return self.nominal(attempt)

        if mode == "full":
            return self.rng.uniform(0.0, self.nominal(attempt))

        if mode == "equal":
            half = self.nominal(attempt) / 2.0
            return half + self.rng.uniform(0.0, half)

        # decorrelated: ignore the curve and drift from the last delay
        if previous is None:
            previous = float(policy.base)
        else:
            previous = _require_real(previous, "previous")
        upper = min(float(policy.cap), max(float(policy.base), previous * 3.0))
        return self.rng.uniform(float(policy.base), upper)

    # -- the schedule ------------------------------------------------------

    def plan(
        self,
        max_attempts: int = 5,
        *,
        deadline: Optional[float] = None,
        retry_after: Optional[Union[int, float, str, datetime]] = None,
    ) -> Schedule:
        """Build the schedule for up to ``max_attempts`` retries.

        ``deadline`` is the total wall-clock budget for waiting, in seconds. A
        retry whose delay would push the running total past it is dropped and
        ``truncated`` is set, so the caller can stop instead of sleeping into a
        timeout it already knows about.

        ``retry_after`` is the server's instruction for the *first* delay. It is
        honoured verbatim -- not jittered, and not capped by the policy, since a
        server asking for 120s means 120s. It is still bounded by ``deadline``:
        if honouring it would blow the budget, no retries are scheduled at all.
        """
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
            raise BackoffError(
                f"max_attempts must be an int, not {type(max_attempts).__name__}"
            )
        if max_attempts < 0:
            raise BackoffError(f"max_attempts must not be negative, got {max_attempts}")

        budget = None if deadline is None else _require_real(deadline, "deadline")

        forced = None
        if retry_after is not None:
            forced = parse_retry_after(retry_after)

        delays = []
        elapsed = 0.0
        truncated = False

        for attempt in range(1, max_attempts + 1):
            if attempt == 1 and forced is not None:
                delay = forced
            else:
                previous = delays[-1] if delays else None
                delay = self.delay_for(attempt, previous=previous)

            if budget is not None and elapsed + delay > budget:
                truncated = True
                break

            delays.append(delay)
            elapsed += delay

        return Schedule(tuple(delays), truncated=truncated)


def is_retryable_status(status: int) -> bool:
    """True for status codes where retrying the same request can succeed."""
    if isinstance(status, bool) or not isinstance(status, int):
        raise BackoffError(f"status must be an int, not {type(status).__name__}")
    if not 100 <= status <= 599:
        raise BackoffError(f"status must be a valid HTTP status, got {status}")
    return status in RETRYABLE_STATUS


def parse_retry_after(
    value: Union[int, float, str, datetime],
    *,
    now: Optional[datetime] = None,
) -> float:
    """Turn a ``Retry-After`` header value into a non-negative number of seconds.

    Accepts the two forms RFC 9110 allows -- delta-seconds (``"120"``) and an
    HTTP-date (``"Wed, 21 Oct 2015 07:28:00 GMT"``) -- plus a ``datetime`` and a
    plain number for convenience. A date in the past yields ``0.0`` rather than
    a negative sleep. Anything else raises :class:`BackoffError`; a header you
    cannot parse is a header you must not guess at.
    """
    if isinstance(value, datetime):
        return _seconds_until(value, now)

    if isinstance(value, bool):
        raise BackoffError("retry_after must not be a bool")

    if isinstance(value, (int, float)):
        return _require_real(value, "retry_after")

    if not isinstance(value, str):
        raise BackoffError(
            f"retry_after must be a str, number or datetime, not {type(value).__name__}"
        )

    text = value.strip()
    if not text:
        raise BackoffError("retry_after is empty")

    if _DELTA_SECONDS.match(text):
        # int() on an arbitrarily long digit string is cheap enough here, but
        # cap the magnitude so a 10k-digit header cannot produce an inf sleep.
        seconds = float(text) if len(text) <= 9 else math.inf
        if not math.isfinite(seconds):
            raise BackoffError("retry_after delta-seconds is out of range")
        return seconds

    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError) as exc:
        raise BackoffError(f"retry_after is not a valid HTTP-date: {value!r}") from exc

    return _seconds_until(when, now)


def _seconds_until(when: datetime, now: Optional[datetime]) -> float:
    """Seconds from ``now`` until ``when``, floored at zero."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if when.tzinfo is None:
        # RFC 9110 dates are always GMT; a naive datetime can only mean UTC.
        when = when.replace(tzinfo=timezone.utc)

    return max(0.0, (when - now).total_seconds())
