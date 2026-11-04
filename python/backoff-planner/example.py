"""Runnable demo for backoff_planner: what a retry loop actually waits.

    python example.py
"""

import random
from datetime import datetime, timedelta, timezone

from backoff_planner import (
    BackoffError,
    RetryPlanner,
    RetryPolicy,
    is_retryable_status,
    parse_retry_after,
)


def show_table(title, plan):
    print(title)
    print(f"  {'retry':>5}  {'delay (s)':>10}  {'elapsed (s)':>12}")
    for index, (delay, elapsed) in enumerate(zip(plan.delays, plan.cumulative()), start=1):
        print(f"  {index:>5}  {delay:>10.3f}  {elapsed:>12.3f}")
    print(f"  total {plan.total:.3f}s, retries {len(plan)}"
          f"{', cut short by the deadline' if plan.truncated else ''}")
    print()


def main():
    # A fleet of clients retrying a flaky endpoint. Full jitter is the default
    # because it spreads them out instead of letting them return in lockstep.
    jittered = RetryPlanner(RetryPolicy(base=0.5, factor=2.0, cap=30.0, jitter="full"),
                            rng=random.Random(2026))
    show_table("full jitter, no deadline:", jittered.plan(6))

    # Same policy, deterministic, so the numbers are reviewable in a runbook.
    deterministic = RetryPlanner(RetryPolicy(base=0.5, factor=2.0, cap=8.0, jitter="none"))
    show_table("no jitter, capped at 8s:", deterministic.plan(6))

    # A caller with a 6-second budget must not schedule a 7.5s sleep.
    show_table("no jitter, 6s deadline:", deterministic.plan(6, deadline=6.0))

    # A 503 with Retry-After: the server's number wins for the first delay.
    show_table("Retry-After: 12, 60s deadline:", deterministic.plan(4, deadline=60.0, retry_after="12"))

    # A server asking for more time than the caller has: no retries at all.
    plan = deterministic.plan(4, deadline=10.0, retry_after="120")
    print(f"Retry-After 120s against a 10s deadline -> {len(plan)} retries, "
          f"truncated={plan.truncated}\n")

    # Retry-After comes in two shapes; both become seconds.
    now = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
    header = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    print(f"parse_retry_after('{header}') = {parse_retry_after(header, now=now)}s")
    print(f"parse_retry_after('120') = {parse_retry_after('120')}s\n")

    # Decide whether a response is worth retrying at all.
    for status in (200, 404, 429, 503, 501):
        verdict = "retry" if is_retryable_status(status) else "give up"
        print(f"  HTTP {status}: {verdict}")
    print()

    # A header you cannot parse is an error, not a guess.
    try:
        parse_retry_after("soon")
    except BackoffError as exc:
        print(f"parse_retry_after('soon') -> BackoffError: {exc}")


if __name__ == "__main__":
    main()
