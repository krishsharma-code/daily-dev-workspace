# backoff-planner

Retry scheduling for Python with the awkward parts handled: capped exponential
backoff, four jitter strategies, a wall-clock deadline the schedule can never
overshoot, and strict parsing of the server's `Retry-After` header. Standard
library only, no dependencies.

It computes delays. It does not sleep, loop, or touch the network — you keep
control of the retry loop, which is where the interesting decisions belong.

```python
from backoff_planner import RetryPlanner, RetryPolicy, is_retryable_status

planner = RetryPlanner(RetryPolicy(base=0.5, factor=2.0, cap=30.0, jitter="full"))
plan = planner.plan(max_attempts=5, deadline=10.0)

for delay in plan:
    ...            # sleep(delay), retry the request, stop on success
if plan.truncated:
    ...            # out of budget: fail now rather than sleep into a timeout
```

## API

| Name | Purpose |
| --- | --- |
| `RetryPolicy(base, factor, cap, jitter)` | Frozen, validated description of the curve. |
| `RetryPlanner(policy=None, *, rng=None)` | Computes delays and schedules. Pass `random.Random(seed)` for reproducible jitter. |
| `planner.nominal(attempt)` | The un-jittered delay for a 1-based attempt, capped. |
| `planner.delay_for(attempt, *, previous=None)` | The delay after jitter. `previous` is only needed by `"decorrelated"`. |
| `planner.plan(max_attempts=5, *, deadline=None, retry_after=None)` | A `Schedule` for the whole retry sequence. |
| `Schedule` | `.delays`, `.total`, `.cumulative()`, `.truncated`, `len()`, iteration. |
| `parse_retry_after(value, *, now=None)` | Header value (delta-seconds, HTTP-date) or `datetime` → seconds, floored at 0. |
| `is_retryable_status(status)` | True for `408, 425, 429, 500, 502, 503, 504`. |

Everything invalid raises `BackoffError`, a `ValueError` subclass: a negative
base, `factor < 1`, `cap < base`, an unknown jitter mode, `attempt=0` (attempts
are 1-based), `NaN`/`inf` anywhere, a `bool` where a number belongs, a status
outside 100–599, or a header that is neither delta-seconds nor an HTTP-date.

## Jitter

Deterministic backoff synchronises a fleet: every client that failed at the
same moment comes back at the same moment. Jitter is what prevents that.

| `jitter` | Delay for attempt *n* | Use when |
| --- | --- | --- |
| `"none"` | `min(base · factorⁿ⁻¹, cap)` | You need reviewable, reproducible numbers. |
| `"full"` (default) | uniform in `0 … nominal` | The general case; best spread for the least code. |
| `"equal"` | `nominal/2 + uniform(0 … nominal/2)` | You want a guaranteed minimum wait and some spread. |
| `"decorrelated"` | uniform in `base … min(cap, 3 · previous)` | Sustained contention; drifts away from the herd instead of following a fixed curve. |

`"decorrelated"` ignores `factor` by design — that is AWS's variant, and it is
the one that keeps a client from re-synchronising after every failure. Its
first delay is drawn from `base … min(cap, 3·base)`; pass `previous=` to
continue an existing chain.

## The deadline is a hard bound

`plan(deadline=t)` never returns a schedule whose total exceeds `t`. A retry
whose delay would cross the boundary is dropped and `truncated` is set, so the
caller learns "no more retries" instead of discovering it as a timeout later.
The boundary itself is inclusive: a 3.5s schedule fits a 3.5s deadline exactly.

`deadline` bounds *waiting*, not the request itself. If your HTTP call may take
2s, subtract that from the budget you pass in — or check the remaining time
before each attempt, which this module cannot do for you.

## Retry-After

A `503` or `429` often carries an instruction, and ignoring it is how a client
gets itself blocked. `plan(retry_after=...)` uses the server's value verbatim
as the *first* delay: not jittered, and not reduced by `cap`, because a server
asking for 120 seconds means 120 seconds. It is still bounded by `deadline` — a
120s instruction against a 10s budget yields an empty schedule and
`truncated=True` rather than a sleep that guarantees failure.

Both RFC 9110 forms are accepted, along with the two obsolete date formats that
still appear in the wild:

```python
parse_retry_after("120")                                  # 120.0
parse_retry_after("Wed, 04 Nov 2026 09:00:30 GMT", now=t)  # 30.0
parse_retry_after("Wednesday, 04-Nov-26 09:00:30 GMT", now=t)  # 30.0
parse_retry_after("Wed Nov  4 09:00:30 2026", now=t)       # 30.0
```

A date in the past returns `0.0`, not a negative sleep. A naive `datetime` or
`now` is treated as UTC, since every HTTP-date is GMT. Anything unparseable
raises: `"soon"`, `"-5"`, `"+5"`, `"5s"`, `"1.5"`, and — importantly —
`"١٢٠"`. That last one is Arabic-Indic digits, which `str.isdigit()` accepts
and `int()` would quietly turn into 120; the parser matches the RFC grammar
(ASCII `[0-9]+`) instead. A delta-seconds value with more than nine digits is
rejected rather than becoming an infinite sleep.

## Status classification

`RETRYABLE_STATUS` covers `408 Request Timeout`, `425 Too Early`, `429 Too Many
Requests`, and `500 / 502 / 503 / 504`. Deliberately excluded are `501 Not
Implemented` and `505 HTTP Version Not Supported`: retrying repeats the same
request against the same incapable server. `400`, `401`, `403`, `404` and `409`
are excluded for the same reason — the request, not the timing, is the problem.

The helper answers "may this be retried?", not "is this safe to retry?".
Nothing here knows whether your `POST` is idempotent; gate retries on an
idempotency key or restrict them to safe methods yourself.

## What this does not do

- No sleeping, no loop, no async. `plan()` returns numbers.
- No state between calls: each `plan()` starts from `base`. Persist the last
  delay yourself if a process restarts mid-chain, or build the schedule once at
  the start and walk it.
- No global coordination between processes. Jitter decorrelates them
  statistically; it is not a rate limiter. Use one of those for a hard cap on
  request rate.
- No timeouts on the request itself — only on the waiting around it.

## Layout

```
python/backoff-planner/
├── README.md
├── backoff_planner.py            # implementation
├── example.py                    # runnable demo
├── conftest.py                   # makes the module importable in tests
└── tests/
    └── test_backoff_planner.py
```

## Run the demo

```bash
cd python/backoff-planner
python example.py
```

It prints a full-jitter schedule, the same policy without jitter, a schedule
cut short by a 6-second deadline, a `Retry-After: 12` schedule, the
"server wants longer than we have" case, both header formats, and a status
table.

## Run the tests

```bash
cd python/backoff-planner
python -m pytest
```

114 tests: the curve (including a 10,000th attempt that would otherwise
overflow a float power), every validation path, all four jitter modes against
their bounds, deadline arithmetic at the boundary, `Retry-After` in every
accepted and rejected shape, status classification, and a seeded randomised
check that no policy/jitter/deadline combination ever schedules more waiting
than the budget allows. `pytest` is needed only to run the suite.

## Example

```python
import random
from backoff_planner import RetryPlanner, RetryPolicy, is_retryable_status, parse_retry_after

planner = RetryPlanner(
    RetryPolicy(base=0.5, factor=2.0, cap=8.0, jitter="none"),
)

planner.plan(5).delays                      # (0.5, 1.0, 2.0, 4.0, 8.0)
planner.plan(5, deadline=3.0).delays        # (0.5, 1.0)  -- 2.0 would cross 3.5s
planner.plan(5, deadline=3.0).truncated     # True
planner.plan(3, retry_after="12").delays    # (12.0, 1.0, 2.0)

is_retryable_status(503)                    # True
is_retryable_status(501)                    # False
parse_retry_after("Wed, 04 Nov 2026 09:00:30 GMT", now=now)   # 30.0
```
