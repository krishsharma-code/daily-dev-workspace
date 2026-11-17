"""A clock that steps backwards must not break the documented guarantees.

Both limiters take an injectable ``clock``. The default, :func:`time.monotonic`,
never moves backwards -- but the documented contract only says "a callable
returning the current time in seconds", which invites ``time.time``.
``time.time`` *does* step backwards: an NTP correction, a container inheriting a
drifted host clock, or an operator setting the date can all move it back by
seconds.

That used to be a real defect for :class:`SlidingWindowCounter`.
``retry_after`` located the admission whose expiry frees the last needed slot by
position -- ``_events[deficit - 1]`` -- which is only correct while the deque is
sorted. A backwards step appends a timestamp *older* than its predecessors, so
position stopped naming the right admission and ``retry_after`` returned a wait
that was far too short, breaking the one promise the README makes about it.

``retry_after`` now takes the largest timestamp among the ``deficit`` entries
that actually have to be pruned, so ordering no longer matters. These tests pin
the fix down and check that the safety direction is unchanged: a backwards clock
may hold a request back, never let an extra one through.
"""

import random

import pytest

from ratelimiter import SlidingWindowCounter, TokenBucket


class SteppingClock:
    """A clock whose reading is set by hand, so it can jump in either direction."""

    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now

    def set(self, value):
        self.now = value


class TestRetryAfterWithABackwardsStep:
    def test_retry_after_is_still_enough_after_a_backwards_step(self):
        """The exact case that used to under-report by 50 seconds.

        ``limit=2`` fills with admissions at t=200 then t=150 (the clock moved
        back), leaving ``_events == [200, 150]`` -- unsorted. Asking for two
        slots indexed ``events[1] == 150`` and answered "10s", but at
        t=150 + 10 the admission at 200 is still inside the window, so
        ``allow(2)`` refused. The binding expiry is the largest timestamp among
        the entries that must go, i.e. 200, so the honest answer is 60s.
        """
        clock = SteppingClock()
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)

        clock.set(100.0)
        assert limiter.allow() is True
        clock.set(200.0)
        assert limiter.allow() is True
        clock.set(150.0)
        assert limiter.allow() is True
        assert limiter.allow() is False
        assert list(limiter._events) == [200.0, 150.0]

        wait = limiter.retry_after(tokens=2)
        assert wait == pytest.approx(60.0, abs=1e-6)

        clock.set(150.0 + wait)
        assert limiter.allow(tokens=2) is True

    def test_retry_after_is_sufficient_across_random_backwards_jumps(self):
        """Property check: whatever the clock does, the promised wait is enough."""
        rng = random.Random(20261117)
        for _ in range(200):
            limit = rng.randint(1, 4)
            window = rng.uniform(0.5, 10.0)
            clock = SteppingClock(rng.uniform(0.0, 1000.0))
            limiter = SlidingWindowCounter(limit=limit, window=window, clock=clock)

            for _ in range(rng.randint(1, 12)):
                # Any direction, including jumps of several windows.
                clock.set(clock.now + rng.uniform(-3 * window, 2 * window))
                limiter.allow(rng.randint(1, limit))

            tokens = rng.randint(1, limit)
            wait = limiter.retry_after(tokens)
            assert wait >= 0.0
            clock.set(clock.now + wait)
            assert limiter.allow(tokens) is True, (
                f"retry_after({tokens}) = {wait} was not enough "
                f"(limit={limit}, window={window})"
            )

    def test_retry_after_is_zero_when_the_window_has_room(self):
        clock = SteppingClock()
        limiter = SlidingWindowCounter(limit=2, window=10.0, clock=clock)
        clock.set(100.0)
        assert limiter.retry_after() == 0.0


class TestSlidingWindowSafetyWithABackwardsStep:
    def test_a_backwards_step_never_over_admits(self):
        clock = SteppingClock()
        limiter = SlidingWindowCounter(limit=3, window=10.0, clock=clock)
        clock.set(50.0)
        assert [limiter.allow() for _ in range(3)] == [True, True, True]

        clock.set(10.0)  # 40 seconds back: nothing has really expired
        assert limiter.allow() is False
        assert limiter.remaining() == 0

    def test_window_still_expires_once_the_clock_catches_up(self):
        clock = SteppingClock()
        limiter = SlidingWindowCounter(limit=1, window=5.0, clock=clock)
        clock.set(20.0)
        assert limiter.allow() is True

        clock.set(19.0)  # backwards step: the admission is still inside
        assert limiter.allow() is False

        clock.set(25.0)  # exactly one window after the admission at 20.0
        assert limiter.allow() is True

    def test_reset_clears_the_window(self):
        clock = SteppingClock()
        limiter = SlidingWindowCounter(limit=1, window=5.0, clock=clock)
        clock.set(100.0)
        assert limiter.allow() is True
        limiter.reset()
        assert limiter.remaining() == 1
        clock.set(1.0)
        assert limiter.allow() is True


class TestTokenBucketWithABackwardsStep:
    def test_acquire_never_reports_a_negative_wait(self):
        """``acquire`` returns "seconds waited"; a backwards clock made it negative."""

        class ScriptedClock:
            """Readings: construction, ``start``, the refilling pass, the return.

            ``start`` is 1000.0 and the successful pass reads 100.0, so the
            elapsed time across the call is -900s -- exactly the case that used
            to come back as a negative "seconds waited".
            """

            readings = [0.0, 1000.0, 100.0, 100.0]

            def __init__(self):
                self.calls = 0

            def __call__(self):
                index = min(self.calls, len(self.readings) - 1)
                self.calls += 1
                return self.readings[index]

        bucket = TokenBucket(
            capacity=1,
            refill_rate=1.0,
            clock=ScriptedClock(),
            sleep=lambda _seconds: None,
            start_full=False,
        )
        # The refill between the two readings grants the token, so the call
        # succeeds even though the clock ended up 900s behind where it started.
        assert bucket.acquire(tokens=1) == 0.0

    def test_backwards_step_does_not_mint_tokens(self):
        clock = SteppingClock()
        bucket = TokenBucket(capacity=10, refill_rate=1.0, clock=clock, start_full=False)

        clock.set(100.0)
        assert bucket.tokens == pytest.approx(10.0)  # refilled to capacity
        assert bucket.try_acquire(10) is True

        clock.set(50.0)  # 50 seconds back
        assert bucket.try_acquire(10) is False
        assert bucket.tokens == pytest.approx(0.0, abs=1e-9)
