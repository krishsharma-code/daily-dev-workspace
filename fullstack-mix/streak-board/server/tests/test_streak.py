"""Tests for the streak engine: weekdays, current/longest runs, completion."""

from datetime import date, timedelta

import pytest

from streak import (
    ALL_DAYS,
    StreakError,
    completion_rate,
    current_streak,
    format_day,
    is_scheduled,
    longest_streak,
    next_scheduled_day,
    normalise_weekdays,
    parse_day,
    previous_scheduled_day,
    scheduled_days,
    streak_summary,
)

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
WEEKDAYS = (MON, TUE, WED, THU, FRI)


def days(*texts):
    return [parse_day(text) for text in texts]


def run_back(end: str, count: int) -> list[date]:
    """``count`` consecutive calendar days ending on ``end`` (inclusive)."""
    last = parse_day(end)
    return [last - timedelta(days=offset) for offset in range(count)][::-1]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_day_accepts_iso_and_date():
    assert parse_day("2026-11-25") == date(2026, 11, 25)
    assert parse_day(date(2026, 1, 1)) == date(2026, 1, 1)


@pytest.mark.parametrize(
    "value", ["2026-1-5", "25-11-2026", "2026-11-25T10:00", "yesterday", ""]
)
def test_parse_day_is_strict(value):
    with pytest.raises(StreakError):
        parse_day(value)


def test_parse_day_rejects_impossible_dates():
    with pytest.raises(StreakError):
        parse_day("2026-02-30")


def test_parse_day_rejects_non_strings():
    with pytest.raises(StreakError):
        parse_day(20261125)
    with pytest.raises(StreakError):
        parse_day(None)


def test_format_day_round_trips():
    assert format_day(parse_day("2026-03-09")) == "2026-03-09"


# ---------------------------------------------------------------------------
# Weekday sets
# ---------------------------------------------------------------------------


def test_weekdays_default_to_every_day():
    assert normalise_weekdays(None) == ALL_DAYS


def test_weekdays_accept_names_numbers_and_strings():
    assert normalise_weekdays(["mon", "wed", "fri"]) == (MON, WED, FRI)
    assert normalise_weekdays("Mon, WED,  fri") == (MON, WED, FRI)
    assert normalise_weekdays([5, 0, 5]) == (MON, SAT)


def test_weekdays_daily_word():
    assert normalise_weekdays("daily") == ALL_DAYS


@pytest.mark.parametrize("value", [[], "", "funday", [7], [-1], [True], True, 9])
def test_weekdays_reject_bad_input(value):
    with pytest.raises(StreakError):
        normalise_weekdays(value)


def test_is_scheduled_respects_weekdays():
    friday = date(2026, 11, 27)
    saturday = date(2026, 11, 28)
    assert is_scheduled(friday, WEEKDAYS)
    assert not is_scheduled(saturday, WEEKDAYS)
    assert is_scheduled(saturday)  # every day by default


def test_previous_and_next_scheduled_day_skip_the_weekend():
    monday = date(2026, 11, 30)
    assert previous_scheduled_day(monday, WEEKDAYS) == date(2026, 11, 27)
    assert next_scheduled_day(date(2026, 11, 27), WEEKDAYS) == monday


def test_previous_and_next_scheduled_day_on_all_days():
    day = date(2026, 11, 25)
    assert previous_scheduled_day(day) == day - timedelta(days=1)
    assert next_scheduled_day(day) == day + timedelta(days=1)


def test_scheduled_days_counts_only_matching_weekdays():
    span = scheduled_days(date(2026, 11, 23), date(2026, 11, 29), WEEKDAYS)
    assert [format_day(day) for day in span] == [
        "2026-11-23",
        "2026-11-24",
        "2026-11-25",
        "2026-11-26",
        "2026-11-27",
    ]


def test_scheduled_days_rejects_reversed_range():
    with pytest.raises(StreakError):
        scheduled_days(date(2026, 11, 29), date(2026, 11, 23))


# ---------------------------------------------------------------------------
# Current streak
# ---------------------------------------------------------------------------


def test_current_streak_counts_consecutive_days():
    history = run_back("2026-11-25", 5)
    assert current_streak(history, "2026-11-25") == 5


def test_current_streak_zero_when_nothing_done():
    assert current_streak([], "2026-11-25") == 0


def test_missed_yesterday_breaks_the_streak():
    history = days("2026-11-20", "2026-11-21", "2026-11-23")
    assert current_streak(history, "2026-11-25") == 0


def test_today_not_done_does_not_break_the_streak_yet():
    history = days("2026-11-23", "2026-11-24")
    # Today (25th) is scheduled and still open: count back from yesterday.
    assert current_streak(history, "2026-11-25") == 2


def test_missing_yesterday_with_today_done_resets_to_one():
    history = days("2026-11-23", "2026-11-25")
    assert current_streak(history, "2026-11-25") == 1


def test_future_checkins_are_ignored():
    history = days("2026-11-25", "2026-11-26", "2026-11-27")
    assert current_streak(history, "2026-11-25") == 1


def test_current_streak_skips_unscheduled_days():
    # Mon/Wed/Fri habit: 20 Nov (Fri), 23 Nov (Mon), 25 Nov (Wed).
    history = days("2026-11-20", "2026-11-23", "2026-11-25")
    assert current_streak(history, "2026-11-25", (MON, WED, FRI)) == 3


def test_unscheduled_checkins_neither_extend_nor_break_a_streak():
    # Saturday is not a Mon/Wed/Fri day, so a check-in then changes nothing.
    scheduled_only = days("2026-11-20", "2026-11-23")
    with_saturday = days("2026-11-20", "2026-11-21", "2026-11-23")
    assert current_streak(scheduled_only, "2026-11-23", (MON, WED, FRI)) == 2
    assert current_streak(with_saturday, "2026-11-23", (MON, WED, FRI)) == 2
    # A missed Monday leaves only the Friday before it standing.
    assert (
        current_streak(days("2026-11-20", "2026-11-21"), "2026-11-23", (MON, WED, FRI))
        == 1
    )


def test_current_streak_on_an_unscheduled_today():
    # Today is Saturday; the habit is Mon/Wed/Fri, so Friday is the last chance.
    history = days("2026-11-23", "2026-11-25", "2026-11-27")
    assert current_streak(history, "2026-11-28", (MON, WED, FRI)) == 3


def test_duplicate_checkins_are_counted_once():
    history = days("2026-11-24", "2026-11-25", "2026-11-25")
    assert current_streak(history, "2026-11-25") == 2


# ---------------------------------------------------------------------------
# Longest streak
# ---------------------------------------------------------------------------


def test_longest_streak_finds_the_best_run():
    history = days(
        "2026-11-01",
        "2026-11-02",
        "2026-11-05",
        "2026-11-06",
        "2026-11-07",
        "2026-11-08",
    )
    assert longest_streak(history) == 4


def test_longest_streak_empty_is_zero():
    assert longest_streak([]) == 0


def test_longest_streak_counts_a_single_day():
    assert longest_streak(days("2026-11-25")) == 1


def test_longest_streak_ignores_unscheduled_gaps():
    # Every Mon/Wed/Fri for two weeks is one unbroken run of six.
    history = days(
        "2026-11-02",
        "2026-11-04",
        "2026-11-06",
        "2026-11-09",
        "2026-11-11",
        "2026-11-13",
    )
    assert longest_streak(history, (MON, WED, FRI)) == 6


def test_longest_streak_is_at_least_the_current_one():
    history = days("2026-11-20", "2026-11-24", "2026-11-25")
    assert longest_streak(history) >= current_streak(history, "2026-11-25")


# ---------------------------------------------------------------------------
# Completion rate and summary
# ---------------------------------------------------------------------------


def test_completion_rate_over_a_window():
    rate = completion_rate(
        days("2026-11-24", "2026-11-25"), "2026-11-23", "2026-11-25"
    )
    assert rate["done"] == 2
    assert rate["scheduled"] == 3
    assert rate["missed"] == ["2026-11-23"]
    assert rate["percent"] == 66.7


def test_completion_rate_counts_only_scheduled_days():
    rate = completion_rate([], "2026-11-23", "2026-11-29", WEEKDAYS)
    assert rate["scheduled"] == 5
    assert rate["done"] == 0
    assert rate["percent"] == 0.0


def test_completion_rate_without_scheduled_days_is_zero_not_a_crash():
    # A Saturday-only habit observed across a Mon-Wed window.
    rate = completion_rate([], "2026-11-23", "2026-11-25", (SAT,))
    assert rate["scheduled"] == 0
    assert rate["percent"] == 0.0


def test_streak_summary_bundles_the_numbers():
    history = days("2026-11-24", "2026-11-25")
    summary = streak_summary(history, "2026-11-25", window_days=7)
    assert summary["current"] == 2
    assert summary["longest"] == 2
    assert summary["done_today"] is True
    assert summary["scheduled_today"] is True
    assert summary["total_checkins"] == 2
    assert summary["window"]["start"] == "2026-11-19"
    assert summary["window"]["days"] == 7


def test_streak_summary_ignores_future_checkins_in_the_total():
    summary = streak_summary(days("2026-11-25", "2026-12-01"), "2026-11-25")
    assert summary["total_checkins"] == 1


@pytest.mark.parametrize("window", [0, -3, 4000, True, 1.5])
def test_streak_summary_rejects_bad_windows(window):
    with pytest.raises(StreakError):
        streak_summary([], "2026-11-25", window_days=window)
