"""Tests for backoff_planner: the curve, the jitter, the deadline and the header."""

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from backoff_planner import (
    RETRYABLE_STATUS,
    BackoffError,
    RetryPlanner,
    RetryPolicy,
    Schedule,
    is_retryable_status,
    parse_retry_after,
)

NOW = datetime(2026, 11, 4, 9, 0, 0, tzinfo=timezone.utc)


def plain(**kwargs):
    """A planner with no jitter, so the curve is exactly predictable."""
    kwargs.setdefault("jitter", "none")
    return RetryPlanner(RetryPolicy(**kwargs))


# -- the curve -------------------------------------------------------------


def test_nominal_is_the_plain_exponential():
    planner = plain(base=0.5, factor=2.0, cap=30.0)
    assert [planner.nominal(n) for n in range(1, 6)] == [0.5, 1.0, 2.0, 4.0, 8.0]


def test_nominal_is_capped():
    planner = plain(base=1.0, factor=2.0, cap=10.0)
    assert planner.nominal(4) == 8.0
    assert planner.nominal(5) == 10.0
    assert planner.nominal(60) == 10.0


def test_nominal_survives_an_enormous_attempt_number():
    # Without the overflow guard this raises OverflowError from the float power.
    planner = plain(base=1.0, factor=2.0, cap=10.0)
    assert planner.nominal(10_000) == 10.0
    assert planner.nominal(2**31) == 10.0


def test_factor_of_one_is_a_flat_delay():
    planner = plain(base=1.5, factor=1.0, cap=10.0)
    assert [planner.nominal(n) for n in (1, 2, 9)] == [1.5, 1.5, 1.5]


def test_fractional_factor():
    planner = plain(base=1.0, factor=1.5, cap=100.0)
    assert planner.nominal(3) == pytest.approx(2.25)


def test_zero_base_stays_zero():
    planner = plain(base=0.0, factor=2.0, cap=10.0)
    assert planner.nominal(5) == 0.0


@pytest.mark.parametrize("attempt", [0, -1, -100])
def test_attempt_is_one_based(attempt):
    with pytest.raises(BackoffError, match="1-based"):
        plain().nominal(attempt)


@pytest.mark.parametrize("attempt", [True, False, 1.0, "2", None])
def test_attempt_must_be_an_int(attempt):
    with pytest.raises(BackoffError, match="attempt must be an int"):
        plain().nominal(attempt)


# -- policy validation -----------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"base": -1}, "base must not be negative"),
        ({"base": float("nan")}, "base must be finite"),
        ({"cap": float("inf")}, "cap must be finite"),
        ({"factor": 0.5}, "factor must be at least 1"),
        ({"base": 5, "cap": 1}, "cap .* is below base"),
        ({"jitter": "gaussian"}, "jitter must be one of"),
        ({"base": True}, "base must be a number"),
        ({"base": "1"}, "base must be a number"),
    ],
)
def test_bad_policies_are_rejected(kwargs, message):
    with pytest.raises(BackoffError, match=message):
        RetryPolicy(**kwargs)


def test_default_policy_is_usable():
    policy = RetryPolicy()
    assert (policy.base, policy.factor, policy.cap) == (0.5, 2.0, 30.0)
    assert policy.jitter == "full"


def test_planner_rejects_a_non_policy():
    with pytest.raises(BackoffError, match="policy must be a RetryPolicy"):
        RetryPlanner({"base": 1})


def test_planner_rejects_a_bad_rng():
    with pytest.raises(BackoffError, match="rng must be a random.Random"):
        RetryPlanner(rng=7)


# -- jitter ----------------------------------------------------------------


def test_no_jitter_is_the_nominal_curve():
    planner = RetryPlanner(RetryPolicy(base=1.0, factor=2.0, cap=30.0, jitter="none"))
    assert [planner.delay_for(n) for n in range(1, 5)] == [1.0, 2.0, 4.0, 8.0]


def test_full_jitter_stays_within_zero_and_nominal():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=2.0, cap=30.0, jitter="full"),
        rng=random.Random(11),
    )
    for attempt in range(1, 8):
        nominal = planner.nominal(attempt)
        for _ in range(50):
            delay = planner.delay_for(attempt)
            assert 0.0 <= delay <= nominal


def test_equal_jitter_keeps_half_the_delay():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=2.0, cap=30.0, jitter="equal"),
        rng=random.Random(3),
    )
    for attempt in range(1, 6):
        nominal = planner.nominal(attempt)
        delay = planner.delay_for(attempt)
        assert nominal / 2.0 <= delay <= nominal


def test_full_jitter_actually_varies():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=1.0, cap=30.0, jitter="full"),
        rng=random.Random(5),
    )
    delays = {planner.delay_for(1) for _ in range(20)}
    assert len(delays) > 1


def test_decorrelated_jitter_bounds_each_step():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=2.0, cap=8.0, jitter="decorrelated"),
        rng=random.Random(2),
    )
    previous = 1.0
    for _ in range(200):
        delay = planner.delay_for(3, previous=previous)
        assert 1.0 <= delay <= min(8.0, max(1.0, previous * 3.0))
        previous = delay


def test_decorrelated_without_previous_starts_at_base():
    planner = RetryPlanner(
        RetryPolicy(base=1.0, factor=2.0, cap=8.0, jitter="decorrelated"),
        rng=random.Random(4),
    )
    for _ in range(20):
        assert 1.0 <= planner.delay_for(1) <= 3.0


def test_decorrelated_respects_a_cap_below_three_times_base():
    planner = RetryPlanner(
        RetryPolicy(base=2.0, factor=2.0, cap=2.5, jitter="decorrelated"),
        rng=random.Random(6),
    )
    for _ in range(50):
        assert 2.0 <= planner.delay_for(2, previous=100.0) <= 2.5


def test_decorrelated_rejects_a_bad_previous():
    planner = RetryPlanner(RetryPolicy(jitter="decorrelated"), rng=random.Random(1))
    with pytest.raises(BackoffError, match="previous must not be negative"):
        planner.delay_for(2, previous=-1.0)


def test_seeded_planners_agree():
    def delays():
        planner = RetryPlanner(RetryPolicy(jitter="full"), rng=random.Random(99))
        return [planner.delay_for(n) for n in range(1, 6)]

    assert delays() == delays()


def test_unseeded_planner_creates_its_own_rng():
    planner = RetryPlanner(RetryPolicy(jitter="full"))
    assert isinstance(planner.rng, random.Random)
    assert planner.rng is planner.rng


# -- schedules -------------------------------------------------------------


def test_plan_without_a_deadline_returns_every_attempt():
    plan = plain().plan(5)
    assert isinstance(plan, Schedule)
    assert len(plan) == 5
    assert plan.delays == (0.5, 1.0, 2.0, 4.0, 8.0)
    assert plan.truncated is False


def test_plan_of_zero_attempts_is_empty():
    plan = plain().plan(0)
    assert plan.delays == ()
    assert plan.total == 0.0
    assert plan.truncated is False


def test_plan_total_and_cumulative():
    plan = plain().plan(4)
    assert plan.total == pytest.approx(7.5)
    assert plan.cumulative() == pytest.approx((0.5, 1.5, 3.5, 7.5))


def test_plan_is_iterable():
    assert list(plain().plan(3)) == [0.5, 1.0, 2.0]


def test_deadline_cuts_the_schedule_short():
    plan = plain().plan(6, deadline=3.0)
    # 0.5 + 1.0 + 2.0 = 3.5 > 3.0, so the third retry is dropped.
    assert plan.delays == (0.5, 1.0)
    assert plan.truncated is True


def test_deadline_exactly_equal_to_the_total_is_allowed():
    plan = plain().plan(3, deadline=3.5)
    assert plan.delays == (0.5, 1.0, 2.0)
    assert plan.truncated is False


def test_zero_deadline_allows_nothing():
    plan = plain().plan(3, deadline=0.0)
    assert plan.delays == ()
    assert plan.truncated is True


def test_zero_deadline_with_zero_base_is_not_truncated():
    plan = plain(base=0.0).plan(3, deadline=0.0)
    assert plan.delays == (0.0, 0.0, 0.0)
    assert plan.truncated is False


def test_truncated_is_false_when_the_deadline_is_merely_large():
    plan = plain().plan(4, deadline=1000.0)
    assert plan.truncated is False


@pytest.mark.parametrize("deadline", [-1, float("nan"), "5", True])
def test_bad_deadlines_are_rejected(deadline):
    with pytest.raises(BackoffError, match="deadline"):
        plain().plan(3, deadline=deadline)


@pytest.mark.parametrize("attempts", [-1, 2.0, True, "3"])
def test_bad_max_attempts_are_rejected(attempts):
    with pytest.raises(BackoffError, match="max_attempts"):
        plain().plan(attempts)


def test_retry_after_sets_the_first_delay_verbatim():
    plan = plain().plan(3, retry_after="120")
    assert plan.delays == (120.0, 1.0, 2.0)


def test_retry_after_is_not_capped_by_the_policy():
    plan = plain(base=1.0, cap=2.0).plan(2, retry_after=120)
    assert plan.delays[0] == 120.0


def test_retry_after_is_not_jittered():
    planner = RetryPlanner(RetryPolicy(base=1.0, jitter="full"), rng=random.Random(1))
    plan = planner.plan(2, retry_after=5)
    assert plan.delays[0] == 5.0


def test_retry_after_beyond_the_deadline_schedules_nothing():
    plan = plain().plan(3, deadline=10.0, retry_after=120)
    assert plan.delays == ()
    assert plan.truncated is True


def test_retry_after_of_zero_still_retries_immediately():
    plan = plain().plan(2, retry_after=0)
    assert plan.delays == (0.0, 1.0)


def test_retry_after_does_not_affect_the_decorrelated_chain_start():
    # The forced first delay becomes "previous", so the second delay drifts from it.
    planner = RetryPlanner(
        RetryPolicy(base=1.0, cap=10.0, jitter="decorrelated"),
        rng=random.Random(8),
    )
    plan = planner.plan(2, retry_after=2.0)
    assert plan.delays[0] == 2.0
    assert 1.0 <= plan.delays[1] <= 6.0


def test_schedule_is_immutable():
    plan = plain().plan(2)
    with pytest.raises(Exception):
        plan.delays = ()


# -- Retry-After parsing ---------------------------------------------------


@pytest.mark.parametrize("value", ["0", "1", "120", " 7 "])
def test_delta_seconds_are_parsed(value):
    assert parse_retry_after(value) == float(value.strip())


def test_numeric_values_pass_through():
    assert parse_retry_after(3) == 3.0
    assert parse_retry_after(2.5) == 2.5


def test_http_date_is_parsed_against_now():
    assert parse_retry_after("Wed, 04 Nov 2026 09:00:30 GMT", now=NOW) == 30.0


def test_rfc850_and_asctime_dates_are_accepted():
    assert parse_retry_after("Wednesday, 04-Nov-26 09:00:30 GMT", now=NOW) == 30.0
    assert parse_retry_after("Wed Nov  4 09:00:30 2026", now=NOW) == 30.0


def test_a_date_in_the_past_is_zero_not_negative():
    assert parse_retry_after("Wed, 04 Nov 2026 08:59:00 GMT", now=NOW) == 0.0


def test_a_naive_now_is_treated_as_utc():
    naive = NOW.replace(tzinfo=None)
    assert parse_retry_after("Wed, 04 Nov 2026 09:00:30 GMT", now=naive) == 30.0


def test_a_datetime_value_is_measured_against_now():
    assert parse_retry_after(NOW + timedelta(seconds=12), now=NOW) == 12.0


def test_a_naive_datetime_value_is_treated_as_utc():
    assert parse_retry_after(datetime(2026, 11, 4, 9, 0, 45), now=NOW) == 45.0


@pytest.mark.parametrize(
    "value",
    ["", "   ", "-5", "+5", "5s", "1.5", "abc", "Wed, 99 Xxx 2026 09:00:00 GMT"],
)
def test_unparseable_values_raise(value):
    with pytest.raises(BackoffError):
        parse_retry_after(value)


def test_negative_numbers_are_rejected():
    with pytest.raises(BackoffError, match="must not be negative"):
        parse_retry_after(-1)


def test_nan_is_rejected():
    with pytest.raises(BackoffError, match="must be finite"):
        parse_retry_after(float("nan"))


def test_bools_are_rejected():
    with pytest.raises(BackoffError, match="bool"):
        parse_retry_after(True)


def test_unsupported_types_are_rejected():
    with pytest.raises(BackoffError, match="must be a str, number or datetime"):
        parse_retry_after(["120"])


def test_an_absurdly_long_delta_seconds_is_rejected_not_infinite():
    with pytest.raises(BackoffError, match="out of range"):
        parse_retry_after("9" * 400)


def test_arabic_indic_digits_are_not_delta_seconds():
    # str.isdigit() would accept these; the regex is ASCII-only on purpose.
    with pytest.raises(BackoffError):
        parse_retry_after("١٢٠")


# -- status classification -------------------------------------------------


def test_retryable_statuses():
    for status in (408, 425, 429, 500, 502, 503, 504):
        assert is_retryable_status(status) is True
    assert RETRYABLE_STATUS == frozenset({408, 425, 429, 500, 502, 503, 504})


@pytest.mark.parametrize("status", [200, 201, 204, 301, 400, 401, 403, 404, 409, 501, 505])
def test_non_retryable_statuses(status):
    assert is_retryable_status(status) is False


@pytest.mark.parametrize("status", [0, 99, 600, 999, -1])
def test_out_of_range_statuses_are_rejected(status):
    with pytest.raises(BackoffError, match="valid HTTP status"):
        is_retryable_status(status)


@pytest.mark.parametrize("status", [True, "500", 500.0, None])
def test_non_int_statuses_are_rejected(status):
    with pytest.raises(BackoffError, match="status must be an int"):
        is_retryable_status(status)


# -- randomised invariants -------------------------------------------------


@pytest.mark.parametrize("mode", ["none", "full", "equal", "decorrelated"])
def test_random_policies_never_exceed_the_deadline(mode):
    rng = random.Random(2026)
    for _ in range(50):
        base = rng.uniform(0.01, 5)
        cap = base * rng.uniform(1, 20)
        policy = RetryPolicy(base=base, factor=rng.uniform(1, 3), cap=cap, jitter=mode)
        deadline = rng.uniform(0, 120)
        plan = RetryPlanner(policy, rng=random.Random(rng.random())).plan(
            rng.randint(0, 8), deadline=deadline
        )
        assert plan.total <= deadline
        assert all(delay >= 0 for delay in plan.delays)


@pytest.mark.parametrize("mode", ["none", "full", "equal", "decorrelated"])
def test_every_delay_respects_the_cap(mode):
    policy = RetryPolicy(base=1.0, factor=2.0, cap=6.0, jitter=mode)
    planner = RetryPlanner(policy, rng=random.Random(1))
    for delay in planner.plan(20).delays:
        assert 0.0 <= delay <= 6.0


def test_plan_does_not_share_state_between_calls():
    planner = plain()
    first = planner.plan(3)
    second = planner.plan(3)
    assert first.delays == second.delays
    assert math.fsum(first.delays) == math.fsum(second.delays)
