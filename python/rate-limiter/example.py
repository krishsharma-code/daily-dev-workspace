"""Runnable tour of the ratelimiter primitives.

Run it directly::

    python example.py

Both examples use a tiny fake clock so the output is deterministic and the
script finishes instantly.
"""

from ratelimiter import RateLimitExceeded, SlidingWindowCounter, TokenBucket


class FakeClock:
    """A monotonic clock you can advance by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def token_bucket_demo() -> None:
    clock = FakeClock()
    # Bucket of 5 tokens, refilling at 2 tokens/second.
    bucket = TokenBucket(capacity=5, refill_rate=2.0, clock=clock, sleep=clock.sleep)

    print("-- token bucket (capacity=5, refill=2/s) --")
    print("first 5 requests:", [bucket.try_acquire() for _ in range(5)])
    print("6th request:", bucket.try_acquire(), "(bucket empty)")
    print(f"time until next token: {bucket.time_until_available():.2f}s")
    waited = bucket.acquire()
    print(f"acquire() waited {waited:.2f}s, bucket now has {bucket.tokens:.1f} tokens")

    try:
        bucket.acquire(tokens=5, timeout=0.5)
    except RateLimitExceeded as exc:
        print("acquire(timeout=0.5) raised:", exc)


def sliding_window_demo() -> None:
    clock = FakeClock()
    limiter = SlidingWindowCounter(limit=3, window=10.0, clock=clock)

    print("\n-- sliding window (limit=3 per 10s) --")
    print("three quick requests:", [limiter.allow() for _ in range(3)])
    print("fourth request:", limiter.allow(), f"(remaining={limiter.remaining()})")
    clock.now += 6
    print("after 6s, still limited:", limiter.allow())
    clock.now += 5  # 11s since the first three admissions
    print("after 11s, allowed again:", limiter.allow(), f"(remaining={limiter.remaining()})")


if __name__ == "__main__":
    token_bucket_demo()
    sliding_window_demo()
