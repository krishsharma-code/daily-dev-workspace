"""Tests for the business-hours calendar."""

from datetime import date, datetime, time, timedelta, timezone

import pytest

from business_hours import BusinessCalendar, NoOpenWindowError


def cal(**kwargs):
    """Mon-Fri, 09:00-17:00 unless the test says otherwise."""
    kwargs.setdefault("hours", ("09:00", "17:00"))
    return BusinessCalendar(**kwargs)


# 2026-11-27 is a Friday; 28-29 are the weekend; 30 is the following Monday.
FRI = date(2026, 11, 27)
MON = date(2026, 11, 30)


def at(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute))


# --------------------------------------------------------------------------
# basic arithmetic
# --------------------------------------------------------------------------


def test_add_within_one_day():
    assert cal().add_business_hours(at(MON, 10), 2) == at(MON, 12)


def test_add_rolls_over_the_close():
    assert cal().add_business_hours(at(FRI, 16), 2) == at(MON, 10)


def test_add_across_a_full_day():
    assert cal().add_business_hours(at(MON, 9), 9) == at(MON + timedelta(days=1), 10)


def test_add_exactly_to_close_stays_on_the_same_day():
    assert cal().add_business_hours(at(MON, 9), 8) == at(MON, 17)


def test_starting_at_close_moves_to_the_next_open():
    assert cal().add_business_hours(at(FRI, 17), 1) == at(MON, 10)


def test_starting_before_open_is_clipped_to_the_open():
    assert cal().add_business_hours(at(MON, 7), 1) == at(MON, 10)


def test_starting_on_a_weekend_skips_to_monday():
    assert cal().add_business_hours(at(FRI + timedelta(days=1), 12), 1) == at(MON, 10)


def test_fractional_hours():
    assert cal().add_business_hours(at(MON, 10), 0.5) == at(MON, 10, 30)
    assert cal().add_business_hours(at(MON, 10), timedelta(minutes=90)) == at(MON, 11, 30)


def test_zero_hours_is_a_no_op():
    start = at(MON, 10)
    assert cal().add_business_hours(start, 0) == start


def test_subtracting_walks_backwards():
    # 1h on Monday takes us to 09:00, the remaining hour lands on Friday.
    assert cal().add_business_hours(at(MON, 10), -2) == at(FRI, 16)


def test_subtracting_from_inside_the_week():
    assert cal().add_business_hours(at(MON, 11), -1) == at(MON, 10)


# --------------------------------------------------------------------------
# holidays
# --------------------------------------------------------------------------


def test_holiday_is_skipped():
    # Friday is closed, so the two hours start counting on Monday morning.
    calendar = cal(holidays=["2026-11-27"])
    assert calendar.add_business_hours(at(FRI, 16), 2) == at(MON, 11)


def test_holiday_accepts_dates_and_datetimes():
    calendar = cal(holidays=[date(2026, 11, 27), datetime(2026, 11, 26, 15, 0)])
    assert calendar.holidays == frozenset({date(2026, 11, 27), date(2026, 11, 26)})


def test_holiday_on_a_weekend_changes_nothing():
    calendar = cal(holidays=["2026-11-28"])
    assert calendar.add_business_hours(at(FRI, 16), 2) == at(MON, 10)


def test_holiday_is_not_a_workday():
    calendar = cal(holidays=["2026-11-27"])
    assert calendar.is_workday(FRI) is False
    assert calendar.is_workday(MON) is True
    assert calendar.is_workday(FRI + timedelta(days=1)) is False


# --------------------------------------------------------------------------
# split windows (lunch break)
# --------------------------------------------------------------------------


def split_calendar():
    return BusinessCalendar.from_windows(
        {0: [("09:00", "13:00"), ("14:00", "18:00")]},  # Monday only
    )


def test_split_window_pauses_over_lunch():
    calendar = split_calendar()
    assert calendar.add_business_hours(at(MON, 12), 2) == at(MON, 15)


def test_split_window_counts_no_lunch_time():
    calendar = split_calendar()
    span = calendar.business_time_between(at(MON, 9), at(MON, 18))
    assert span == timedelta(hours=8)


def test_split_window_moves_past_the_break_when_closed():
    calendar = split_calendar()
    assert calendar.add_business_hours(at(MON, 13, 30), 1) == at(MON, 15)


def test_overlapping_windows_are_merged():
    calendar = BusinessCalendar.from_windows({0: [("09:00", "12:00"), ("11:00", "15:00")]})
    assert calendar.business_time_between(at(MON, 9), at(MON, 16)) == timedelta(hours=6)


def test_day_missing_from_windows_is_closed():
    calendar = BusinessCalendar.from_windows({1: [("09:00", "17:00")]})
    assert calendar.is_workday(MON) is False
    assert calendar.is_workday(MON + timedelta(days=1)) is True


# --------------------------------------------------------------------------
# elapsed time between two moments
# --------------------------------------------------------------------------


def test_business_time_between_skips_the_weekend():
    assert cal().business_time_between(at(FRI, 16), at(MON, 11)) == timedelta(hours=3)


def test_business_time_between_is_clipped_to_the_window():
    assert cal().business_time_between(at(MON, 6), at(MON, 20)) == timedelta(hours=8)


def test_business_time_between_closed_period_is_zero():
    saturday = FRI + timedelta(days=1)
    assert cal().business_time_between(at(saturday, 10), at(saturday, 18)) == timedelta(0)


def test_business_time_between_rejects_reversed_range():
    with pytest.raises(ValueError):
        cal().business_time_between(at(MON, 11), at(MON, 10))


def test_business_time_between_round_trips_with_add():
    calendar = cal()
    start = at(MON, 11)
    deadline = calendar.add_business_hours(start, 9)
    assert calendar.business_time_between(start, deadline) == timedelta(hours=9)


# --------------------------------------------------------------------------
# open / closed queries
# --------------------------------------------------------------------------


def test_is_open_boundaries():
    calendar = cal()
    assert calendar.is_open(at(MON, 9)) is True
    assert calendar.is_open(at(MON, 16, 59)) is True
    assert calendar.is_open(at(MON, 17)) is False
    assert calendar.is_open(at(MON, 8, 59)) is False


def test_next_open_from_the_weekend():
    assert cal().next_open(at(FRI + timedelta(days=2), 12)) == at(MON, 9)


def test_next_open_when_already_open():
    calendar = cal()
    assert calendar.next_open(at(MON, 10)) == at(MON, 10)


def test_next_open_exclusive_skips_the_current_window():
    calendar = cal()
    assert calendar.next_open(at(MON, 10), inclusive=False) == at(MON + timedelta(days=1), 9)


def test_segments_reflect_holidays():
    calendar = cal(holidays=["2026-11-27"])
    assert calendar.segments(FRI) == ()
    assert calendar.segments(MON) == ((at(MON, 9), at(MON, 17)),)


# --------------------------------------------------------------------------
# timezones
# --------------------------------------------------------------------------


def test_timezone_is_applied_to_naive_input():
    calendar = cal(tz="Asia/Kolkata")
    result = calendar.add_business_hours(datetime(2026, 11, 30, 16), 2)
    assert result.utcoffset() == timedelta(hours=5, minutes=30)
    assert result.hour == 10 and result.day == 1


def test_timezone_normalises_aware_input():
    calendar = cal(tz="Asia/Kolkata")
    utc = datetime(2026, 11, 30, 4, 30, tzinfo=timezone.utc)  # 10:00 IST
    assert calendar.add_business_hours(utc, 1).hour == 11


def test_timezone_accepts_a_tzinfo_object():
    calendar = cal(tz=timezone.utc)
    result = calendar.add_business_hours(datetime(2026, 11, 30, 9), 1)
    assert result.tzinfo is timezone.utc
    assert result.hour == 10


def test_aware_input_rejected_when_calendar_has_no_timezone():
    calendar = cal()
    with pytest.raises(ValueError):
        calendar.add_business_hours(datetime(2026, 11, 30, 9, tzinfo=timezone.utc), 1)


# --------------------------------------------------------------------------
# configuration errors
# --------------------------------------------------------------------------


def test_close_must_be_after_open():
    with pytest.raises(ValueError):
        BusinessCalendar(("17:00", "09:00"))


def test_equal_open_and_close_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar(("09:00", "09:00"))


def test_hours_accepts_strings_and_time_objects():
    calendar = BusinessCalendar((time(9, 30), "18:30"))
    assert calendar.segments(MON) == ((at(MON, 9, 30), at(MON, 18, 30)),)


def test_bad_time_string_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar(("nine", "17:00"))


def test_empty_workdays_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar(("09:00", "17:00"), workdays=[])


def test_out_of_range_weekday_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar(("09:00", "17:00"), workdays=[0, 7])


def test_empty_windows_mapping_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar.from_windows({})


def test_weekday_with_no_windows_is_dropped():
    calendar = BusinessCalendar.from_windows({0: [("09:00", "17:00")], 1: []})
    assert calendar.is_workday(MON + timedelta(days=1)) is False


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError):
        BusinessCalendar(("09:00", "17:00"), tz="Mars/Olympus_Mons")


def test_repr_is_readable():
    text = repr(cal(holidays=["2026-11-27"], tz="Asia/Kolkata"))
    assert "workdays=" in text and "holidays=1" in text


def test_no_open_window_error_is_a_value_error():
    assert issubclass(NoOpenWindowError, ValueError)
