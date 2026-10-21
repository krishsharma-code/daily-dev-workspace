# rate-limiter

Small, dependency-free rate-limiting primitives for Python. Two strategies are
provided, each holding all of its state in the instance and touching neither the
network nor the filesystem:

| Class | Strategy |
| --- | --- |
| `TokenBucket` | A classic token bucket: up to `capacity` tokens, refilling at `refill_rate` tokens/second. A call proceeds only when enough tokens are available, and can otherwise wait. |
| `SlidingWindowCounter` | Counts admissions in a trailing window and refuses once the window is full. Smoother than a fixed window, since a burst can't straddle a boundary. |

Time is read through an injectable `clock` (default `time.monotonic`) and
waiting goes through an injectable `sleep` (default `time.sleep`). Injecting
both lets you drive a limiter from a fake clock in tests, so behaviour is
deterministic and no test ever sleeps in real time.

## API

**`TokenBucket(capacity, refill_rate, *, clock=None, sleep=None, start_full=True)`**

| Member | Purpose |
| --- | --- |
| `tokens` | Tokens currently available (after refilling). |
| `try_acquire(tokens=1)` | Take tokens if available; returns `True`/`False`, consuming only on success. |
| `time_until_available(tokens=1)` | Seconds until `tokens` could be acquired (`0.0` if available now). |
| `acquire(tokens=1, timeout=None)` | Block until tokens are available, then consume them; returns the seconds waited. Raises `RateLimitExceeded` if `timeout` would be exceeded. |

**`SlidingWindowCounter(limit, window, *, clock=None)`**

| Member | Purpose |
| --- | --- |
| `allow(tokens=1)` | Admit `tokens` requests if the window has room; records the admission only on success. |
| `remaining()` | Admissions still available in the current window. |
| `reset()` | Forget all recorded admissions. |

Invalid arguments (`capacity <= 0`, `tokens` above the capacity/limit, a
negative timeout, ...) raise `ValueError`.

## Layout

```
python/rate-limiter/
├── README.md
├── ratelimiter.py            # implementation
├── example.py                # runnable demo
├── conftest.py               # makes the module importable in tests
└── tests/
    └── test_ratelimiter.py
```

## Run the demo

```bash
cd python/rate-limiter
python example.py
```

## Run the tests

```bash
cd python/rate-limiter
python -m pytest
```

No third-party dependencies are required at runtime; `pytest` is needed only to
run the test suite.

## Example

```python
from ratelimiter import RateLimitExceeded, SlidingWindowCounter, TokenBucket

# At most 5 calls, refilling at 2 per second.
bucket = TokenBucket(capacity=5, refill_rate=2.0)
if not bucket.try_acquire():
    wait = bucket.time_until_available()
    ...  # back off, or block instead:
    bucket.acquire(tokens=1, timeout=1.0)  # raises RateLimitExceeded if too slow

# At most 3 calls per 10 seconds.
limiter = SlidingWindowCounter(limit=3, window=10.0)
if limiter.allow():
    ...  # handle the request
```

To test without real time passing, pass a fake clock:

```python
class FakeClock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds

clock = FakeClock()
bucket = TokenBucket(capacity=1, refill_rate=1.0, clock=clock, sleep=clock.sleep)
bucket.try_acquire()          # True
bucket.try_acquire()          # False
clock.now += 1.0
bucket.try_acquire()          # True again
```
