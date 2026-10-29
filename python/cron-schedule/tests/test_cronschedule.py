"""Tests for cronschedule."""

import datetime as dt

import pytest

from cronschedule import CronError, CronExpression, ScheduleExhausted


def at(*args, **kwargs):
    return dt.datetime(*args, **kwargs)


class TestParsing:
    def test_plain_expression_keeps_normalised_text(self):
        schedule = CronExpression("  0   9  *  *  1-5 ")
        assert str(schedule) == "0 9 * * 1-5"

    def test_every_minute_covers_all_values(self):
        schedule = CronExpression("* * * * *")
        assert len(schedule.minutes) == 60
        assert len(schedule.hours) == 24
        assert len(schedule.days_of_month) == 31
        assert len(schedule.months) == 12
        assert len(schedule.days_of_week) == 7

    def test_steps_expand_from_field_minimum(self):
        schedule = CronExpression("*/15 */6 * * *")
        assert schedule.minutes == frozenset({0, 15, 30, 45})
        assert schedule.hours == frozenset({0, 6, 12, 18})

    def test_range_with_step(self):
        schedule = CronExpression("5-20/5 * * * *")
        assert schedule.minutes == frozenset({5, 10, 15, 20})

    def test_single_value_with_step_runs_to_maximum(self):
        schedule = CronExpression("50/5 * * * *")
        assert schedule.minutes == frozenset({50, 55})

    def test_lists_and_ranges_combine(self):
        schedule = CronExpression("0 9,17-18,23 * * *")
        assert schedule.hours == frozenset({9, 17, 18, 23})

    def test_month_and_day_names(self):
        schedule = CronExpression("0 0 * jan-mar mon,wed,fri")
        assert schedule.months == frozenset({1, 2, 3})
        assert schedule.days_of_week == frozenset({1, 3, 5})

    def test_names_are_case_insensitive(self):
        assert CronExpression("0 0 * * MON") == CronExpression("0 0 * * mon")

    def test_seven_is_an_alias_for_sunday(self):
        assert CronExpression("0 0 * * 7").days_of_week == frozenset({0})

    def test_macros_expand(self):
        assert CronExpression("@daily") == CronExpression("0 0 * * *")
        assert CronExpression("@hourly") == CronExpression("0 * * * *")
        assert CronExpression("@weekly") == CronExpression("0 0 * * 0")
        assert CronExpression("@monthly") == CronExpression("0 0 1 * *")
        assert CronExpression("@yearly") == CronExpression("0 0 1 1 *")
        assert CronExpression("@ANNUALLY") == CronExpression("0 0 1 1 *")

    def test_equality_and_hash_follow_normalised_form(self):
        first = CronExpression("0 9 * * 1")
        second = CronExpression("0 9 * * MON")
        assert first == second
        assert len({first, second}) == 1

    def test_repr_round_trips_text(self):
        assert repr(CronExpression("0 9 * * *")) == "CronExpression('0 9 * * *')"

    def test_equality_with_other_types_is_not_an_error(self):
        assert CronExpression("0 9 * * *") != "0 9 * * *"

    @pytest.mark.parametrize("expression", ["", "   ", "* * * *", "* * * * * *"])
    def test_wrong_field_count_rejected(self, expression):
        with pytest.raises(CronError):
            CronExpression(expression)

    def test_non_string_rejected(self):
        with pytest.raises(TypeError):
            CronExpression(5)

    def test_unknown_macro_rejected(self):
        with pytest.raises(CronError, match="unknown macro"):
            CronExpression("@fortnightly")

    @pytest.mark.parametrize("expression", [
        "60 * * * *",      # minute out of range
        "* 24 * * *",      # hour out of range
        "* * 0 * *",       # day-of-month out of range
        "* * 32 * *",
        "* * * 13 *",      # month out of range
        "* * * * 8",       # day-of-week out of range
        "* * * 0 *",
    ])
    def test_out_of_range_values_rejected(self, expression):
        with pytest.raises(CronError, match="outside"):
            CronExpression(expression)

    @pytest.mark.parametrize("expression", [
        "*/0 * * * *",
        "*/x * * * *",
        "5/ * * * *",
        "/5 * * * *",
        "1-2-3 * * * *",
        "1,,2 * * * *",
    ])
    def test_malformed_terms_rejected(self, expression):
        with pytest.raises(CronError):
            CronExpression(expression)

    def test_reversed_range_rejected(self):
        with pytest.raises(CronError, match="reversed"):
            CronExpression("0 17-9 * * *")

    def test_unknown_name_rejected(self):
        with pytest.raises(CronError, match="not a number"):
            CronExpression("0 0 * * funday")


class TestMatching:
    def test_matches_ignores_seconds(self):
        schedule = CronExpression("30 9 * * *")
        assert schedule.matches(at(2026, 10, 29, 9, 30, 47, 123456))

    def test_matches_is_false_off_schedule(self):
        schedule = CronExpression("30 9 * * *")
        assert not schedule.matches(at(2026, 10, 29, 9, 31))
        assert not schedule.matches(at(2026, 10, 29, 10, 30))

    def test_matches_requires_datetime(self):
        with pytest.raises(TypeError):
            CronExpression("* * * * *").matches("2026-10-29 09:30")

    def test_weekday_matching_uses_sunday_zero(self):
        # 2026-10-29 is a Thursday.
        thursday = CronExpression("0 0 * * 4")
        sunday = CronExpression("0 0 * * 0")
        assert thursday.matches(at(2026, 10, 29))
        assert not sunday.matches(at(2026, 10, 29))
        assert sunday.matches(at(2026, 11, 1))

    def test_both_day_fields_restricted_uses_or_semantics(self):
        # Classic Vixie cron: the 13th *or* any Friday.
        schedule = CronExpression("0 0 13 * FRI")
        assert schedule.matches(at(2026, 10, 13))    # Tuesday the 13th
        assert schedule.matches(at(2026, 10, 30))    # a Friday, not the 13th
        assert not schedule.matches(at(2026, 10, 29))  # Thursday the 29th

    def test_only_one_day_field_restricted_uses_and_semantics(self):
        schedule = CronExpression("0 0 13 * *")
        assert schedule.matches(at(2026, 10, 13))
        assert not schedule.matches(at(2026, 10, 30))

        weekday_only = CronExpression("0 0 * * FRI")
        assert weekday_only.matches(at(2026, 10, 30))
        assert not weekday_only.matches(at(2026, 10, 29))


class TestNextAfter:
    def test_is_strictly_after(self):
        schedule = CronExpression("* * * * *")
        moment = at(2026, 10, 29, 21, 2)
        assert schedule.next_after(moment) == at(2026, 10, 29, 21, 3)
        assert schedule.next_after(at(2026, 10, 29, 21, 2, 48)) == at(
            2026, 10, 29, 21, 3)

    def test_seconds_are_dropped(self):
        schedule = CronExpression("* * * * *")
        assert schedule.next_after(at(2026, 10, 29, 21, 2, 59, 999999)) == at(
            2026, 10, 29, 21, 3)

    def test_quarter_hour_boundary(self):
        schedule = CronExpression("*/15 * * * *")
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2026, 10, 29, 21, 15)
        assert schedule.next_after(at(2026, 10, 29, 21, 15)) == at(2026, 10, 29, 21, 30)

    def test_rolls_over_to_tomorrow(self):
        schedule = CronExpression("0 9 * * *")
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2026, 10, 30, 9, 0)

    def test_rolls_over_to_next_month(self):
        schedule = CronExpression("0 0 1 * *")
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2026, 11, 1, 0, 0)

    def test_rolls_over_to_next_year(self):
        schedule = CronExpression("0 0 1 1 *")
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2027, 1, 1, 0, 0)

    def test_rolls_over_month_end(self):
        schedule = CronExpression("0 0 31 * *")
        # October has 31 days, so the 29th reaches the 31st first.
        assert schedule.next_after(at(2026, 10, 29)) == at(2026, 10, 31, 0, 0)
        # From the 31st, the next 31st is December's, since November is short.
        assert schedule.next_after(at(2026, 10, 31, 1, 0)) == at(2026, 12, 31, 0, 0)

    def test_leap_day_skips_non_leap_years(self):
        schedule = CronExpression("0 0 29 2 *")
        assert schedule.next_after(at(2026, 10, 29)) == at(2028, 2, 29, 0, 0)

    def test_next_weekday(self):
        schedule = CronExpression("0 12 * * MON")
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2026, 11, 2, 12, 0)

    def test_sunday_schedule(self):
        schedule = CronExpression("30 6 * * 0")
        assert schedule.next_after(at(2026, 10, 29)) == at(2026, 11, 1, 6, 30)

    def test_business_hours_window(self):
        schedule = CronExpression("0 9-17 * * *")
        assert schedule.next_after(at(2026, 10, 29, 10, 30)) == at(2026, 10, 29, 11, 0)
        assert schedule.next_after(at(2026, 10, 29, 17, 0)) == at(2026, 10, 30, 9, 0)
        assert schedule.next_after(at(2026, 10, 29, 21, 2)) == at(2026, 10, 30, 9, 0)

    def test_skips_hours_without_allowed_minutes(self):
        schedule = CronExpression("30 9 * * *")
        # 09:31 is past the only minute of the only hour.
        assert schedule.next_after(at(2026, 10, 29, 9, 31)) == at(2026, 10, 30, 9, 30)

    def test_weekend_expression_from_friday(self):
        schedule = CronExpression("0 8 * * SAT,SUN")
        # Friday 30 October 2026 -> Saturday 31 October.
        assert schedule.next_after(at(2026, 10, 30, 9, 0)) == at(2026, 10, 31, 8, 0)

    def test_aware_datetime_keeps_its_timezone(self):
        tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
        schedule = CronExpression("0 9 * * *")
        result = schedule.next_after(at(2026, 10, 29, 21, 2, tzinfo=tz))
        assert result == at(2026, 10, 30, 9, 0, tzinfo=tz)
        assert result.tzinfo is tz

    def test_requires_datetime(self):
        with pytest.raises(TypeError):
            CronExpression("* * * * *").next_after("2026-10-29")

    def test_impossible_date_raises(self):
        with pytest.raises(ScheduleExhausted):
            CronExpression("0 0 31 2 *").next_after(at(2026, 10, 29))

    def test_impossible_date_is_a_cron_error(self):
        assert issubclass(ScheduleExhausted, CronError)

    def test_or_semantics_produces_the_earlier_date(self):
        schedule = CronExpression("0 0 13 * FRI")
        # From 29 October 2026: Friday the 30th comes before 13 November.
        assert schedule.next_after(at(2026, 10, 29)) == at(2026, 10, 30, 0, 0)

    def test_long_dormant_schedule_still_resolves(self):
        # Nearly four years of empty space between two leap days.
        schedule = CronExpression("0 0 29 2 *")
        assert schedule.next_after(at(2028, 3, 1)) == at(2032, 2, 29, 0, 0)


class TestIterAfter:
    def test_returns_requested_count_in_order(self):
        schedule = CronExpression("0 * * * *")
        times = schedule.iter_after(at(2026, 10, 29, 21, 2), 3)
        assert times == (
            at(2026, 10, 29, 22, 0),
            at(2026, 10, 29, 23, 0),
            at(2026, 10, 30, 0, 0),
        )

    def test_zero_count_returns_empty_tuple(self):
        assert CronExpression("0 * * * *").iter_after(at(2026, 10, 29), 0) == ()

    def test_negative_count_rejected(self):
        with pytest.raises(ValueError):
            CronExpression("0 * * * *").iter_after(at(2026, 10, 29), -1)

    def test_non_integer_count_rejected(self):
        with pytest.raises(TypeError):
            CronExpression("0 * * * *").iter_after(at(2026, 10, 29), "3")

    def test_times_are_strictly_increasing(self):
        times = CronExpression("*/20 * * * *").iter_after(at(2026, 10, 29), 10)
        assert all(later > earlier for earlier, later in zip(times, times[1:]))


class TestDescribe:
    def test_every_minute(self):
        assert CronExpression("* * * * *").describe() == "every minute"

    def test_single_minute_and_hour(self):
        assert CronExpression("30 9 * * *").describe() == "at minute 30, past hour 9"

    def test_minute_list_and_hour_range(self):
        assert CronExpression("0,30 9-17 * * *").describe() == (
            "at minutes 0,30, past hours 9-17")

    def test_weekday_names_spelled_out(self):
        assert CronExpression("0 9 * * 1-5").describe() == (
            "at minute 0, past hour 9, on Monday, Tuesday, Wednesday, Thursday, Friday")

    def test_month_names_spelled_out(self):
        assert CronExpression("0 0 1 1,7 *").describe() == (
            "at minute 0, past hour 0, on day(s) 1 of the month, in January, July")

    def test_both_day_fields_join_with_or(self):
        assert CronExpression("0 9 13 * FRI").describe() == (
            "at minute 0, past hour 9, on day(s) 13 of the month or on Friday")

    def test_compresses_long_runs(self):
        assert CronExpression("0 0 * * *").describe() == (
            "at minute 0, past hour 0")


class TestAgainstReferenceScan:
    """Cross-check the jump-based search against a naive minute scanner."""

    EXPRESSIONS = [
        "* * * * *",
        "*/15 * * * *",
        "0 9 * * *",
        "0 9-17 * * 1-5",
        "30 6 * * 0",
        "0 0 1 * *",
        "0 0 13 * FRI",
        "*/7 3-5 * * *",
        "0 0 31 * *",
        "15 22 * 10,11 *",
    ]

    HORIZON_DAYS = 200

    @classmethod
    def _reference_next(cls, schedule, moment, horizon_days=HORIZON_DAYS):
        cursor = (moment + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
        for _ in range(horizon_days * 24 * 60):
            if schedule.matches(cursor):
                return cursor
            cursor += dt.timedelta(minutes=1)
        return None

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_matches_brute_force(self, expression):
        schedule = CronExpression(expression)
        for start in (
            at(2026, 10, 29, 21, 2),
            at(2026, 10, 31, 23, 59),
            at(2027, 2, 28, 12, 0),
            at(2028, 2, 28, 23, 0),
        ):
            reference = self._reference_next(schedule, start)
            actual = schedule.next_after(start)
            if reference is None:
                # The scanner ran out of room; the answer must be beyond it.
                assert actual > start + dt.timedelta(days=self.HORIZON_DAYS)
            else:
                assert actual == reference
