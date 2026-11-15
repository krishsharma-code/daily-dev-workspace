"""Numeric arguments are coerced once, at the boundary.

Regression cover for a defect where ``TokenBucket`` validated ``tokens`` with
:func:`float` but then did arithmetic on the caller's original object::

    >>> bucket.try_acquire(Decimal("1"))
    TypeError: unsupported operand type(s) for -=: 'float' and 'decimal.Decimal'

``Decimal`` compares happily against a ``float``, so the validation gate passed
and the ``TypeError`` escaped from inside the limiter -- breaking the documented
promise that every rejection is a ``ValueError`` and that untrusted
configuration can be validated with a single ``except ValueError``.

The fix is that the validation helpers return the coerced ``float`` and every
call site uses it, so any object with a working ``__float__`` now behaves
exactly like the float it represents.
"""

import math
from decimal import Decimal
from fractions import Fraction

import pytest

import ratelimiter
from ratelimiter import SlidingWindowCounter, TokenBucket


class FakeClock:
    """A deterministic clock whose ``sleep`` advances it."""

    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class OnlyFloatable:
    """A number that supports *nothing* except ``float()``.

    Deliberately defines no comparison or arithmetic dunders, so any internal
    ``tokens > capacity`` or ``tokens - available`` on the raw object raises
    ``TypeError`` instead of quietly working.
    """

    def __init__(self, value):
        self._value = value

    def __float__(self):
        return float(self._value)

    def __repr__(self):
        return f"OnlyFloatable({self._value!r})"


def foreign(value):
    """The same numeric value as ``Decimal``, ``Fraction`` and float-only."""
    return [
        pytest.param(Decimal(str(value)), id="decimal"),
        pytest.param(Fraction(value, 1), id="fraction"),
        pytest.param(OnlyFloatable(value), id="only-floatable"),
    ]


ONE = foreign(1)
TWO = foreign(2)
ZERO = foreign(0)


class TestTokenBucketAcceptsForeignNumerics:
    @pytest.mark.parametrize("value", ONE)
    def test_try_acquire_consumes_one_token(self, value):
        clock = FakeClock()
        bucket = TokenBucket(5, 1.0, clock=clock, sleep=clock.sleep)

        assert bucket.try_acquire(value) is True
        assert bucket.tokens == pytest.approx(4.0)

    @pytest.mark.parametrize("value", ONE)
    def test_try_acquire_refuses_when_short(self, value):
        clock = FakeClock()
        bucket = TokenBucket(1, 1.0, clock=clock, sleep=clock.sleep)
        bucket.try_acquire()

        assert bucket.try_acquire(value) is False
        assert bucket.tokens == pytest.approx(0.0)

    @pytest.mark.parametrize("value", ONE)
    def test_time_until_available_does_the_arithmetic(self, value):
        clock = FakeClock()
        bucket = TokenBucket(2, 0.5, clock=clock, sleep=clock.sleep)
        bucket.try_acquire(2)

        # One token short at half a token per second.
        assert bucket.time_until_available(value) == pytest.approx(2.0)

    @pytest.mark.parametrize("value", ONE)
    def test_acquire_blocks_and_reports_the_wait(self, value):
        clock = FakeClock()
        bucket = TokenBucket(1, 2.0, clock=clock, sleep=clock.sleep)
        bucket.try_acquire()

        waited = bucket.acquire(value)

        assert waited == pytest.approx(0.5)
        assert bucket.tokens == pytest.approx(0.0)

    def test_exceeded_request_carries_a_float(self):
        clock = FakeClock()
        bucket = TokenBucket(1, 1.0, clock=clock, sleep=clock.sleep)
        bucket.try_acquire()

        with pytest.raises(ratelimiter.RateLimitExceeded) as excinfo:
            bucket.acquire(Decimal("1"), timeout=0.1)

        # A handler formats this into a header; it must not be the caller's
        # object with its own repr and arithmetic rules.
        assert isinstance(excinfo.value.retry_after, float)
        assert isinstance(excinfo.value.requested, float)
        assert excinfo.value.requested == pytest.approx(1.0)


class TestForeignNumericsStillValidated:
    @pytest.mark.parametrize("value", TWO)
    def test_above_capacity_is_a_value_error(self, value):
        bucket = TokenBucket(1, 1.0)

        with pytest.raises(ValueError):
            bucket.try_acquire(value)

    @pytest.mark.parametrize("value", ZERO)
    def test_zero_is_a_value_error(self, value):
        bucket = TokenBucket(5, 1.0)

        with pytest.raises(ValueError):
            bucket.try_acquire(value)

    def test_decimal_nan_is_a_value_error(self):
        bucket = TokenBucket(5, 1.0)

        with pytest.raises(ValueError):
            bucket.try_acquire(Decimal("NaN"))

    def test_decimal_infinity_is_a_value_error(self):
        bucket = TokenBucket(5, 1.0)

        with pytest.raises(ValueError):
            bucket.try_acquire(Decimal("Infinity"))

    def test_decimal_timeout_is_coerced(self):
        clock = FakeClock()
        bucket = TokenBucket(1, 1.0, clock=clock, sleep=clock.sleep)
        bucket.try_acquire()

        waited = bucket.acquire(1, timeout=Decimal("5"))

        assert waited == pytest.approx(1.0)


class TestSlidingWindowAcceptsForeignNumerics:
    @pytest.mark.parametrize("value", ONE)
    def test_allow_and_retry_after(self, value):
        clock = FakeClock()
        limiter = SlidingWindowCounter(2, 10.0, clock=clock)

        assert limiter.allow(value) is True
        assert limiter.remaining() == 1
        assert limiter.retry_after(value) == 0.0

        assert limiter.allow(value) is True
        assert limiter.allow(value) is False
        assert limiter.retry_after(value) == pytest.approx(10.0)

    @pytest.mark.parametrize("value", TWO)
    def test_above_limit_is_a_value_error(self, value):
        limiter = SlidingWindowCounter(1, 10.0, clock=FakeClock())

        with pytest.raises(ValueError):
            limiter.allow(value)

    def test_decimal_limit_is_accepted(self):
        limiter = SlidingWindowCounter(Decimal("3"), 10.0, clock=FakeClock())

        assert limiter.limit == 3
        assert isinstance(limiter.limit, int)


class TestNoTypeErrorEscapes:
    """The whole point of the boundary: one ``except ValueError`` is enough."""

    @pytest.mark.parametrize("value", ONE)
    @pytest.mark.parametrize(
        "call",
        [
            lambda b, v: b.try_acquire(v),
            lambda b, v: b.time_until_available(v),
            lambda b, v: b.acquire(v, timeout=0.0),
            lambda b, v: b.acquire(v),
        ],
        ids=["try_acquire", "time_until_available", "acquire-timeout", "acquire"],
    )
    def test_only_value_error_or_success(self, value, call):
        clock = FakeClock()
        bucket = TokenBucket(5, 1.0, clock=clock, sleep=clock.sleep)
        bucket.try_acquire(5)  # drain it so the blocking path is exercised

        try:
            call(bucket, value)
        except ValueError:
            pass  # acceptable: documented rejection
        except ratelimiter.RateLimitExceeded:
            pass  # acceptable: documented refusal
        # Any TypeError/OverflowError propagates and fails the test.

    def test_arithmetic_never_uses_the_raw_object(self):
        clock = FakeClock()
        bucket = TokenBucket(3, 1.0, clock=clock, sleep=clock.sleep)

        # ``OnlyFloatable`` raises on every dunder except ``__float__``; a
        # successful call proves the coerced float is what did the work.
        assert bucket.try_acquire(OnlyFloatable(2)) is True
        assert math.isclose(bucket.tokens, 1.0)
