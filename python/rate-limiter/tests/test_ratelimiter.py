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
