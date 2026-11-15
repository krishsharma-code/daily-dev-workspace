"""``retry_after()`` must actually be enough to get back in.

The README promises, and :meth:`SlidingWindowCounter.retry_after` documents,
that ``sleep(retry_after(n))`` followed by ``allow(n)`` always succeeds. That
was false: the wait was computed as ``expiry + window - now``, a difference of
two rounded quantities, so it could land a fraction of a ULP short of the
expiry instant. ``_prune`` then kept the admission, and ``allow`` refused --
``Retry-After`` values that are off by an epsilon are worse than useless to a
client that honours them exactly.

``_wait_until_expiry`` now rounds up until the wait satisfies the same
predicate ``_prune`` uses. These tests pin that down with a sweep rather than a
single lucky case, because the failure only shows up for particular
``now``/``window``/``limit`` combinations.
"""

import random

import pytest

from ratelimiter import SlidingWindowCounter


class FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def drain(limiter, tokens=1):
    """Fill the window so the next ``allow(tokens)`` has to wait."""
    while limiter.allow(tokens):
        pass


class TestRetryAfterIsSufficient:
    @pytest.mark.parametrize(
        "limit,window,now",
        [
            (1, 2.5, 3.6),
            (1, 2.5, 6.2),
            (3, 10.0, 60.6),
            (4, 10.0, 64.60000000000001),
            (5, 1.0, 3.4000000000000004),
            (1, 10.0, 0.1),
            (2, 10.0, 0.2),
            (4, 10.0, 66.0),
            (6, 2.5, 17.299999999999997),
        ],
    )
    def test_regression_cases(self, limit, window, now):
        clock = FakeClock(start=now)
        limiter = SlidingWindowCounter(limit, window, clock=clock)
        drain(limiter)

        wait = limiter.retry_after()
        clock.sleep(wait)

        assert limiter.allow() is True

    @pytest.mark.parametrize("seed", range(5))
    def test_sweep(self, seed):
        """Random clocks and windows: the promise must hold every time."""
        rng = random.Random(seed)
        for _ in range(120):
            limit = rng.randint(1, 6)
            window = rng.choice([1.0, 2.5, 10.0, 0.3])
            clock = FakeClock(start=rng.choice([0.0, 0.1, 3.6, 60.6, 1e3, 1e5]))
            limiter = SlidingWindowCounter(limit, window, clock=clock)

            # A few admissions at scattered times, then drain.
            for _ in range(rng.randint(0, 4)):
                clock.now += rng.choice([0.0, 0.05, 0.3, 1.0, 2.0, window])
                limiter.allow()
            drain(limiter)

            tokens = rng.randint(1, limit)
            wait = limiter.retry_after(tokens)
            clock.sleep(wait)

            assert limiter.allow(tokens) is True, (
                f"limit={limit} window={window} tokens={tokens} wait={wait!r}"
            )

    def test_multiple_tokens_free_up_together(self):
        clock = FakeClock()
        limiter = SlidingWindowCounter(4, 10.0, clock=clock)
        assert limiter.allow(4) is True

        wait = limiter.retry_after(2)
        clock.sleep(wait)

        assert limiter.allow(2) is True


class TestRetryAfterIsNotWasteful:
    """Rounding up must stay in the nanosecond range, not add real delay."""

    @pytest.mark.parametrize("window", [0.3, 1.0, 2.5, 10.0, 60.0])
    def test_overshoot_is_negligible(self, window):
        clock = FakeClock(start=1234.5678)
        limiter = SlidingWindowCounter(3, window, clock=clock)
        drain(limiter)

        wait = limiter.retry_after()

        assert wait == pytest.approx(window, abs=1e-9)
        assert wait >= window

    def test_zero_when_there_is_room(self):
        limiter = SlidingWindowCounter(3, 10.0, clock=FakeClock())

        assert limiter.retry_after() == 0.0

    def test_large_clock_reading_stays_tight(self):
        # A monotonic clock that has been running for a long time: the ULP of
        # ``now`` is coarser, and the padding has to stay proportional to that
        # rather than to the window.
        clock = FakeClock(start=1e9)
        limiter = SlidingWindowCounter(2, 1.0, clock=clock)
        drain(limiter)

        wait = limiter.retry_after()

        assert 1.0 <= wait < 1.0 + 1e-3
