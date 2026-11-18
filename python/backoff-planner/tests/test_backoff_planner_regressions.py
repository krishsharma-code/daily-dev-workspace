"""Regressions from the code-quality audit of backoff_planner.

Each test here pins a defect found during review, so the behaviour cannot
quietly regress. The suite is deliberately separate from
``test_backoff_planner.py``: that file covers the design, this one covers the
boundaries that were wrong.

Findings covered
----------------
1. ``parse_retry_after("0000000120")`` raised ``BackoffError``. The magnitude
   guard measured the *string width*, not the value, so a legal RFC 9110
   delta-seconds spelling was rejected. (``parse_retry_after``)
2. ``nominal(10**400)`` raised ``OverflowError`` out of the module, breaking the
   documented contract that everything invalid raises ``BackoffError``.
   (``RetryPlanner.nominal``)
3. ``parse_retry_after(..., now="2026-11-04")`` raised ``AttributeError`` for
   the same reason: ``now`` was the one argument never validated.
   (``_seconds_until``)
4. ``plan(max_attempts=10**9)`` would build a list of a billion floats. The
   count is caller-supplied, so it needed a ceiling. (``RetryPlanner.plan``)
"""

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from backoff_planner import (
    MAX_ATTEMPTS,
    BackoffError,
    RetryPlanner,
    RetryPolicy,
    parse_retry_after,
)

NOW = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


def plain(**kwargs):
    """A planner with no jitter, so the curve is exactly predictable."""
    kwargs.setdefault("jitter", "none")
    return RetryPlanner(RetryPolicy(**kwargs))


# -- finding 1: leading zeros are not a magnitude ---------------------------


@pytest.mark.parametrize(
    "header, expected",
    [
        ("0000000120", 120.0),
        ("0000000000", 0.0),
        ("0000000000000000000", 0.0),
        ("000120", 120.0),
        ("120", 120.0),
    ],
)
def test_padded_delta_seconds_parse_to_their_value(header, expected):
    # RFC 9110 spells delta-seconds as 1*DIGIT, so leading zeros are legal and
    # the guard has to look at the value rather than at the string length.
    assert parse_retry_after(header) == expected


def test_padding_cannot_smuggle_a_huge_delay_past_the_guard():
    # Ten significant digits is still out of range, however much padding sits
    # in front of it -- the fix must not become a bypass.
    with pytest.raises(BackoffError, match="out of range"):
        parse_retry_after("00000000001234567890")


def test_the_nine_digit_boundary_is_unchanged():
    assert parse_retry_after("999999999") == 999_999_999.0
    with pytest.raises(BackoffError, match="out of range"):
        parse_retry_after("1000000000")


def test_a_padded_value_still_feeds_the_schedule_verbatim():
    planner = plain(base=0.5, factor=2.0, cap=30.0)
    plan = planner.plan(2, retry_after="0000000012")
    assert plan.delays[0] == 12.0


# -- finding 2: enormous attempts stay on the BackoffError path --------------


def test_nominal_of_an_int_too_large_for_a_float_is_capped():
    # (attempt - 1) * log(factor) cannot be represented as a float here, so the
    # multiplication raises OverflowError unless it is caught.
    planner = plain(base=1.0, factor=2.0, cap=10.0)
    assert planner.nominal(10**400) == 10.0


def test_delay_for_an_int_too_large_for_a_float_is_capped():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=2.0, cap=10.0, jitter="full"),
        rng=random.Random(1),
    )
    delay = planner.delay_for(10**400)
    assert 0.0 <= delay <= 10.0


def test_a_huge_attempt_never_escapes_as_overflow_error():
    planner = plain(base=1.0, factor=3.0, cap=5.0)
    for attempt in (10**308, 10**400):
        assert planner.nominal(attempt) == 5.0


# -- finding 3: `now` is validated like every other argument ----------------


@pytest.mark.parametrize("bad_now", ["2026-11-04", 0, None.__class__, object()])
def test_a_non_datetime_now_raises_backoff_error(bad_now):
    header = "Wed, 04 Nov 2026 09:00:30 GMT"
    with pytest.raises(BackoffError, match="now must be a datetime"):
        parse_retry_after(header, now=bad_now)


def test_a_non_datetime_now_is_rejected_for_a_datetime_value_too():
    with pytest.raises(BackoffError, match="now must be a datetime"):
        parse_retry_after(NOW + timedelta(seconds=30), now="now")


def test_a_valid_now_still_works_in_both_shapes():
    header = (NOW + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(header, now=NOW) == pytest.approx(30.0)
    assert parse_retry_after(header, now=NOW.replace(tzinfo=None)) == pytest.approx(30.0)


# -- finding 4: the schedule has a ceiling ----------------------------------


def test_max_attempts_is_exported_and_positive():
    assert isinstance(MAX_ATTEMPTS, int) and MAX_ATTEMPTS > 0


def test_plan_rejects_an_attempt_count_above_the_ceiling():
    planner = plain()
    with pytest.raises(BackoffError, match="at most"):
        planner.plan(MAX_ATTEMPTS + 1)


def test_plan_accepts_the_ceiling_itself():
    # The boundary is inclusive, and must stay cheap enough to build.
    planner = plain(base=0.5, factor=2.0, cap=30.0)
    plan = planner.plan(MAX_ATTEMPTS, deadline=1.0)
    assert len(plan) <= 3 and plan.truncated is True


def test_plan_still_rejects_negative_and_non_integer_counts():
    planner = plain()
    with pytest.raises(BackoffError, match="not be negative"):
        planner.plan(-1)
    with pytest.raises(BackoffError, match="must be an int"):
        planner.plan(True)
    with pytest.raises(BackoffError, match="must be an int"):
        planner.plan(3.0)


# -- the existing contract still holds after the fixes ----------------------


def test_every_invalid_input_is_still_a_backoff_error():
    # BackoffError subclasses ValueError, so a caller catching ValueError is
    # unaffected -- but nothing may escape as TypeError/OverflowError.
    planner = plain()
    for call in (
        lambda: parse_retry_after("soon"),
        lambda: parse_retry_after(""),
        lambda: parse_retry_after(True),
        lambda: parse_retry_after(float("nan")),
        lambda: planner.nominal(0),
        lambda: planner.plan(-1),
        lambda: planner.plan(MAX_ATTEMPTS + 1),
        lambda: RetryPolicy(factor=0.5),
        lambda: RetryPolicy(jitter="nope"),
    ):
        with pytest.raises(BackoffError):
            call()
        assert issubclass(BackoffError, ValueError)
