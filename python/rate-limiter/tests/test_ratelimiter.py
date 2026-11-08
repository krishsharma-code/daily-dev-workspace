"""Tests for the ratelimiter primitives."""

import pytest

from ratelimiter import RateLimitExceeded, SlidingWindowCounter, TokenBucket


class FakeClock:
    """A monotonic clock advanced by hand, with a matching ``sleep``."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()


def make_bucket(clock, capacity=5, refill_rate=2.0, **kwargs):
    return TokenBucket(
        capacity=capacity,
        refill_rate=refill_rate,
        clock=clock,
        sleep=clock.sleep,
        **kwargs,
    )


class TestTokenBucketValidation:
    def test_non_positive_capacity_rejected(self, clock):
        with pytest.raises(ValueError):
            TokenBucket(capacity=0, refill_rate=1.0, clock=clock)

    def test_non_positive_refill_rejected(self, clock):
        with pytest.raises(ValueError):
            TokenBucket(capacity=1, refill_rate=0.0, clock=clock)

    def test_acquire_zero_tokens_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.try_acquire(0)

    def test_acquire_more_than_capacity_rejected(self, clock):
        bucket = make_bucket(clock, capacity=5)
        with pytest.raises(ValueError):
            bucket.try_acquire(6)

    def test_negative_timeout_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.acquire(timeout=-1)


class TestTokenBucket:
    def test_starts_full(self, clock):
        bucket = make_bucket(clock, capacity=5)
        assert bucket.tokens == 5
        assert [bucket.try_acquire() for _ in range(5)] == [True] * 5

    def test_refuses_when_empty(self, clock):
        bucket = make_bucket(clock, capacity=2)
        assert bucket.try_acquire()
        assert bucket.try_acquire()
        assert bucket.try_acquire() is False

    def test_refills_over_time(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        for _ in range(5):
            bucket.try_acquire()
        assert bucket.tokens == 0
        clock.now += 1.0  # 2 tokens back
        assert bucket.tokens == pytest.approx(2.0)
        assert bucket.try_acquire(2)

    def test_refill_capped_at_capacity(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=10.0)
        bucket.try_acquire(5)
        clock.now += 100.0
        assert bucket.tokens == 5

    def test_start_empty(self, clock):
        bucket = make_bucket(clock, capacity=5, start_full=False)
        assert bucket.tokens == 0
        assert bucket.try_acquire() is False

    def test_time_until_available(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        assert bucket.time_until_available() == 0.0
        bucket.try_acquire(5)
        # Need 1 token at 2 tokens/second -> 0.5s.
        assert bucket.time_until_available() == pytest.approx(0.5)

    def test_acquire_waits_until_available(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        bucket.try_acquire(5)
        waited = bucket.acquire()
        assert waited == pytest.approx(0.5)
        assert clock.now == pytest.approx(0.5)

    def test_acquire_returns_immediately_when_available(self, clock):
        bucket = make_bucket(clock)
        assert bucket.acquire() == 0.0
        assert clock.now == 0.0

    def test_acquire_raises_on_timeout(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        bucket.try_acquire(5)
        with pytest.raises(RateLimitExceeded):
            bucket.acquire(timeout=0.25)
        # A timed-out acquire must not consume anything.
        assert clock.now == 0.0
        assert bucket.tokens == 0

    def test_acquire_succeeds_within_timeout(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        bucket.try_acquire(5)
        waited = bucket.acquire(timeout=1.0)
        assert waited == pytest.approx(0.5)

    def test_backwards_clock_does_not_credit_tokens(self, clock):
        bucket = make_bucket(clock, capacity=5, refill_rate=2.0)
        bucket.try_acquire(5)
        clock.now -= 5.0  # clock jumps backwards
        assert bucket.tokens == 0
        clock.now += 5.0
        assert bucket.tokens == 0


class TestSlidingWindowValidation:
    def test_non_positive_limit_rejected(self, clock):
        with pytest.raises(ValueError):
            SlidingWindowCounter(limit=0, window=1.0, clock=clock)

    def test_non_positive_window_rejected(self, clock):
        with pytest.raises(ValueError):
            SlidingWindowCounter(limit=1, window=0.0, clock=clock)

    def test_zero_tokens_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError):
            limiter.allow(0)

    def test_more_tokens_than_limit_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError):
            limiter.allow(4)


class TestSlidingWindowCounter:
    def test_allows_up_to_limit(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=10.0, clock=clock)
        assert [limiter.allow() for _ in range(3)] == [True, True, True]
        assert limiter.allow() is False
        assert limiter.remaining() == 0

    def test_events_expire_after_window(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        assert limiter.allow()
        assert limiter.allow()
        clock.now += 9.0
        assert limiter.allow() is False
        clock.now += 2.0  # 11s past the first two admissions
        assert limiter.allow() is True

    def test_partial_expiry_frees_one_slot(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow()
        clock.now += 5.0
        limiter.allow()
        clock.now += 5.0  # first admission now 10s old, second is 5s old
        assert limiter.remaining() == 1
        assert limiter.allow() is True
        assert limiter.allow() is False

    def test_burst_of_tokens(self, clock):
        limiter = SlidingWindowCounter(limit=5, window=1.0, clock=clock)
        assert limiter.allow(3) is True
        assert limiter.remaining() == 2
        assert limiter.allow(3) is False
        assert limiter.remaining() == 2

    def test_reset_clears_state(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(2)
        assert limiter.remaining() == 0
        limiter.reset()
        assert limiter.remaining() == 2
        assert limiter.allow()

    def test_boundary_is_exclusive(self, clock):
        # An event exactly one window old is considered expired.
        limiter = SlidingWindowCounter(limit=1, window=10.0, clock=clock)
        assert limiter.allow()
        clock.now += 10.0
        assert limiter.allow() is True


class TestNonFiniteParametersRejected:
    """``nan`` / ``inf`` must fail fast instead of building a broken limiter."""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_token_bucket_capacity_rejected(self, clock, bad):
        with pytest.raises(ValueError):
            TokenBucket(capacity=bad, refill_rate=1.0, clock=clock)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_token_bucket_refill_rate_rejected(self, clock, bad):
        with pytest.raises(ValueError):
            TokenBucket(capacity=1.0, refill_rate=bad, clock=clock)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_sliding_window_window_rejected(self, clock, bad):
        with pytest.raises(ValueError):
            SlidingWindowCounter(limit=3, window=bad, clock=clock)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_sliding_window_limit_rejected(self, clock, bad):
        # Previously ``limit=inf`` leaked an ``OverflowError`` from ``int()``;
        # it must be a plain ``ValueError`` like every other bad argument.
        with pytest.raises(ValueError):
            SlidingWindowCounter(limit=bad, window=1.0, clock=clock)

    def test_nan_capacity_would_have_never_admitted(self, clock):
        # Regression guard for the reported defect: before the fix this built a
        # bucket whose ``tokens`` was ``nan`` and which refused every request.
        with pytest.raises(ValueError):
            TokenBucket(capacity=float("nan"), refill_rate=1.0, clock=clock)


class TestNonFiniteTokensRejected:
    """A ``nan``/``inf`` token count must raise, not be silently swallowed."""

    def test_try_acquire_nan_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.try_acquire(float("nan"))

    def test_try_acquire_inf_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.try_acquire(float("inf"))

    def test_time_until_available_nan_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.time_until_available(float("nan"))

    def test_acquire_nan_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.acquire(float("nan"))

    def test_allow_nan_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError):
            limiter.allow(float("nan"))

    def test_allow_inf_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError):
            limiter.allow(float("inf"))


class TestValidInputStillWorks:
    """The new guards must not disturb ordinary, finite usage."""

    def test_bucket_still_admits_and_refills(self, clock):
        bucket = make_bucket(clock, capacity=2, refill_rate=1.0)
        assert bucket.try_acquire() is True
        assert bucket.try_acquire() is True
        assert bucket.try_acquire() is False
        clock.now += 2.0
        assert bucket.try_acquire() is True

    def test_counter_still_expires(self, clock):
        limiter = SlidingWindowCounter(limit=1, window=5.0, clock=clock)
        assert limiter.allow() is True
        assert limiter.allow() is False
        clock.now += 5.0
        assert limiter.allow() is True


class TestWholeNumberCounts:
    """``limit`` and integer ``tokens`` must be whole numbers, not truncated."""

    def test_fractional_limit_rejected(self, clock):
        # Previously ``int(2.9)`` silently produced a limit of 2 -- a budget
        # halved with no error at all.
        with pytest.raises(ValueError, match="whole number"):
            SlidingWindowCounter(limit=2.9, window=1.0, clock=clock)

    def test_integral_float_limit_accepted(self, clock):
        limiter = SlidingWindowCounter(limit=3.0, window=1.0, clock=clock)
        assert limiter.limit == 3
        assert isinstance(limiter.limit, int)

    def test_fractional_allow_tokens_rejected(self, clock):
        # Previously this escaped as ``TypeError: 'float' object cannot be
        # interpreted as an integer`` from ``range()`` inside ``allow``.
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="whole number"):
            limiter.allow(1.5)

    def test_fractional_tokens_does_not_mutate_state(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError):
            limiter.allow(1.5)
        assert limiter.remaining() == 3

    def test_integral_float_tokens_accepted(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        assert limiter.allow(2.0) is True
        assert limiter.remaining() == 1

    def test_fractional_retry_after_tokens_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="whole number"):
            limiter.retry_after(0.5)


class TestTextInputRejected:
    """Text is refused, not coerced, so config mistakes surface immediately."""

    @pytest.mark.parametrize("bad", ["3", b"3"])
    def test_bucket_capacity_text_rejected(self, clock, bad):
        with pytest.raises(ValueError, match="must be a number"):
            TokenBucket(capacity=bad, refill_rate=1.0, clock=clock)

    def test_counter_limit_text_rejected(self, clock):
        with pytest.raises(ValueError, match="must be a number"):
            SlidingWindowCounter(limit="3", window=1.0, clock=clock)

    def test_try_acquire_text_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="must be a number"):
            bucket.try_acquire("1")

    def test_allow_text_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="must be a number"):
            limiter.allow("1")

    def test_none_tokens_rejected_as_value_error(self, clock):
        # ``None > 0`` used to raise TypeError; the documented contract is
        # ValueError for every invalid argument.
        bucket = make_bucket(clock)
        with pytest.raises(ValueError):
            bucket.try_acquire(None)


class TestErrorMessagesAreAccurate:
    """The message must name the real problem, not a downstream symptom."""

    def test_inf_tokens_reports_non_finite(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="finite"):
            bucket.try_acquire(float("inf"))

    def test_acquire_inf_tokens_reports_non_finite(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="finite"):
            bucket.acquire(float("inf"))

    def test_oversized_tokens_reports_capacity(self, clock):
        bucket = make_bucket(clock, capacity=5)
        with pytest.raises(ValueError, match="bucket capacity"):
            bucket.try_acquire(6)

    def test_oversized_allow_reports_limit(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="than the limit"):
            limiter.allow(4)


class TestTimeoutValidation:
    """A ``nan`` timeout used to defeat the deadline and block forever."""

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_timeout_rejected(self, clock, bad):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="timeout"):
            bucket.acquire(timeout=bad)

    def test_zero_timeout_allowed_when_tokens_available(self, clock):
        bucket = make_bucket(clock)
        assert bucket.acquire(timeout=0) == 0.0

    def test_zero_timeout_raises_when_empty(self, clock):
        bucket = make_bucket(clock, capacity=1, refill_rate=1.0)
        bucket.try_acquire(1)
        with pytest.raises(RateLimitExceeded):
            bucket.acquire(timeout=0)

    def test_text_timeout_rejected(self, clock):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="timeout"):
            bucket.acquire(timeout="1")

    def test_bad_timeout_does_not_consume_tokens(self, clock):
        bucket = make_bucket(clock, capacity=2)
        with pytest.raises(ValueError):
            bucket.acquire(timeout=float("nan"))
        assert bucket.tokens == 2


class TestRetryAfter:
    def test_zero_when_window_has_room(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        assert limiter.retry_after() == 0.0

    def test_full_window_reports_oldest_expiry(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(2)
        clock.now += 4.0
        assert limiter.retry_after() == pytest.approx(6.0)

    def test_burst_needs_more_slots_than_one_expiry(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(1)  # t=0
        clock.now += 5.0
        limiter.allow(1)  # t=5
        clock.now += 1.0  # t=6
        # One slot frees at t=10, a second at t=15.
        assert limiter.retry_after(1) == pytest.approx(4.0)
        assert limiter.retry_after(2) == pytest.approx(9.0)

    def test_waiting_for_retry_after_actually_admits(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(2)
        clock.now += 3.0
        wait = limiter.retry_after(2)
        assert wait == pytest.approx(7.0)
        clock.now += wait
        assert limiter.allow(2) is True

    def test_partial_wait_does_not_admit(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(2)
        clock.now += limiter.retry_after(2) - 0.001
        assert limiter.allow(2) is False

    def test_more_tokens_than_limit_rejected(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        with pytest.raises(ValueError, match="than the limit"):
            limiter.retry_after(3)

    def test_does_not_record_an_admission(self, clock):
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        limiter.allow(1)
        limiter.retry_after(1)
        assert limiter.remaining() == 1


class TestBooleanIsNotANumber:
    """``bool`` is an ``int`` subclass; it must not pass as 1 or 0.

    ``limit`` and ``capacity`` are routinely fed from configuration, and a
    feature flag or a ``KEY in env`` expression evaluates to ``bool``. Accepting
    it silently shrinks a budget to a single slot.
    """

    def test_bool_capacity_rejected(self, clock):
        with pytest.raises(ValueError, match="not bool"):
            TokenBucket(capacity=True, refill_rate=1.0, clock=clock)

    def test_bool_refill_rate_rejected(self, clock):
        with pytest.raises(ValueError, match="not bool"):
            TokenBucket(capacity=1.0, refill_rate=False, clock=clock)

    def test_bool_limit_rejected(self, clock):
        with pytest.raises(ValueError, match="not bool"):
            SlidingWindowCounter(limit=True, window=1.0, clock=clock)

    def test_bool_window_rejected(self, clock):
        with pytest.raises(ValueError, match="not bool"):
            SlidingWindowCounter(limit=1, window=True, clock=clock)

    def test_bool_tokens_does_not_consume(self, clock):
        bucket = make_bucket(clock, capacity=5)
        with pytest.raises(ValueError, match="not bool"):
            bucket.try_acquire(True)
        assert bucket.tokens == 5

    def test_bool_allow_tokens_does_not_record(self, clock):
        limiter = SlidingWindowCounter(limit=3, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="not bool"):
            limiter.allow(True)
        assert limiter.remaining() == 3

    @pytest.mark.parametrize("bad", [True, False])
    def test_bool_timeout_rejected(self, clock, bad):
        bucket = make_bucket(clock)
        with pytest.raises(ValueError, match="timeout"):
            bucket.acquire(timeout=bad)


class TestHugeIntegersAreRejectedAsValueError:
    """``float(10 ** 400)`` raises ``OverflowError``; it must not escape.

    The module promises that every rejection is a ``ValueError`` so callers can
    validate untrusted configuration with one ``except``. ``OverflowError`` is
    not a ``ValueError`` subclass, so a huge integer used to break that promise.
    """

    def test_bucket_capacity_and_refill_rejected(self, clock):
        with pytest.raises(ValueError, match="finite"):
            TokenBucket(capacity=10**400, refill_rate=1.0, clock=clock)
        with pytest.raises(ValueError, match="finite"):
            TokenBucket(capacity=1.0, refill_rate=10**400, clock=clock)

    def test_counter_limit_and_window_rejected(self, clock):
        with pytest.raises(ValueError, match="finite"):
            SlidingWindowCounter(limit=10**400, window=1.0, clock=clock)
        with pytest.raises(ValueError, match="finite"):
            SlidingWindowCounter(limit=1, window=10**400, clock=clock)

    def test_timeout_rejected_without_consuming_tokens(self, clock):
        bucket = make_bucket(clock, capacity=2)
        with pytest.raises(ValueError, match="timeout"):
            bucket.acquire(timeout=10**400)
        assert bucket.tokens == 2

    @pytest.mark.parametrize("bad", [10**400, -(10**400)])
    def test_one_except_value_error_covers_it(self, clock, bad):
        for build in (
            lambda: TokenBucket(capacity=bad, refill_rate=1.0, clock=clock),
            lambda: SlidingWindowCounter(limit=1, window=bad, clock=clock),
        ):
            with pytest.raises(ValueError):
                build()


class TestRateLimitExceededCarriesNumbers:
    """Handlers should not have to parse prose to set ``Retry-After``."""

    def test_retry_after_matches_the_computed_wait(self, clock):
        bucket = make_bucket(clock, capacity=1, refill_rate=2.0)
        bucket.try_acquire(1)
        expected = bucket.time_until_available(1)
        with pytest.raises(RateLimitExceeded) as excinfo:
            bucket.acquire(tokens=1, timeout=0.0)
        assert excinfo.value.retry_after == pytest.approx(expected)
        assert excinfo.value.requested == 1

    def test_message_still_reads_as_prose(self, clock):
        bucket = make_bucket(clock, capacity=1, refill_rate=1.0)
        bucket.try_acquire(1)
        with pytest.raises(RateLimitExceeded, match="exceeding timeout"):
            bucket.acquire(timeout=0.1)

    def test_direct_construction_still_works(self):
        exc = RateLimitExceeded("boom")
        assert str(exc) == "boom"
        assert exc.retry_after is None
        assert exc.requested is None

    def test_header_can_be_built_from_the_attribute(self, clock):
        import math

        bucket = make_bucket(clock, capacity=1, refill_rate=0.5)
        bucket.try_acquire(1)
        with pytest.raises(RateLimitExceeded) as excinfo:
            bucket.acquire(timeout=0.0)
        assert math.ceil(excinfo.value.retry_after) == 2


class TestAcquireRequiresClockProgress:
    """A frozen clock used to make ``acquire`` sleep forever, silently."""

    class FrozenClock:
        """Never advances, no matter how often it is slept on."""

        def __init__(self):
            self.sleeps = 0

        def __call__(self):
            return 100.0

        def sleep(self, seconds):
            self.sleeps += 1

    def test_frozen_clock_raises_instead_of_spinning(self):
        frozen = self.FrozenClock()
        bucket = TokenBucket(
            capacity=1.0,
            refill_rate=1.0,
            clock=frozen,
            sleep=frozen.sleep,
            start_full=False,
        )
        with pytest.raises(RuntimeError, match="clock did not advance"):
            bucket.acquire()
        assert frozen.sleeps == 5

    def test_advancing_clock_still_waits_normally(self, clock):
        bucket = make_bucket(clock, capacity=1, refill_rate=1.0)
        bucket.try_acquire(1)
        assert bucket.acquire() == pytest.approx(1.0)
