"""Robustness tests: hostile input, calendar limits, and search invariants.

These complement ``test_cronschedule.py``. That file checks that the parser
and the search produce the *right* answers for well-formed input; this one
checks that malformed input fails through the documented ``CronError`` path
and that ``next_after`` keeps its invariants across a spread of expressions.
"""

import datetime as dt

import pytest

from cronschedule import CronError, CronExpression, ScheduleExhausted


def at(*args, **kwargs):
    return dt.datetime(*args, **kwargs)


class TestHostileInput:
    """Bad input must surface as CronError, never as a bare ValueError."""

    @pytest.mark.parametrize("expression", [
        "\u00b2 * * * *",        # superscript two: str.isdigit() is True
        "* \u00b2 * * *",
        "* * \u00b2 * *",
        "* * * \u00b2 *",
        "* * * * \u00b2",
    ])
    def test_unicode_digits_in_value_raise_cron_error(self, expression):
        with pytest.raises(CronError):
            CronExpression(expression)

    @pytest.mark.parametrize("expression", [
        "*/\u00b2 * * * *",
        "5-\u00b2 * * * *",
        "0 0 * * mon-\u00b2",
    ])
    def test_unicode_digits_in_step_or_range_raise_cron_error(self, expression):
        with pytest.raises(CronError):
            CronExpression(expression)

    @pytest.mark.parametrize("expression", [
        "\u0665 * * * *",        # Arabic-Indic five
        "\uff15 * * * *",        # fullwidth five
    ])
    def test_non_ascii_digits_are_rejected_not_silently_accepted(
            self, expression):
        # These used to parse. Cron fields are ASCII digits, so they are now
        # rejected through the same error path as any other bad token.
        with pytest.raises(CronError, match="not a number"):
            CronExpression(expression)

    def test_rejection_is_a_value_error_subclass(self):
        # Callers often catch ValueError rather than CronError; that must work.
        with pytest.raises(ValueError):
            CronExpression("0 0 * * funday")

    @pytest.mark.parametrize("expression", [
        "  \t ",                 # whitespace only
        "@",                     # lone macro marker
        "@daily extra",          # macro plus trailing text
        "0 0 * *",               # four fields
        "0 0 * * * *",           # six fields
    ])
    def test_structural_nonsense_raises_cron_error(self, expression):
        with pytest.raises(CronError):
            CronExpression(expression)

    def test_leading_zeros_are_accepted(self):
        assert CronExpression("05 09 * * *") == CronExpression("5 9 * * *")

    def test_step_larger_than_field_is_not_an_error(self):
        # "*/100" over minutes yields {0}: one value, no overflow, no loop.
        assert CronExpression("*/100 * * * *").minutes == frozenset({0})


class TestCalendarLimits:
    """The search must not leak OverflowError when datetime runs out."""

    def test_next_after_datetime_max_raises_schedule_exhausted(self):
        schedule = CronExpression("* * * * *")
        with pytest.raises(ScheduleExhausted):
            schedule.next_after(dt.datetime.max)

    def test_next_after_near_datetime_max_raises_schedule_exhausted(self):
        schedule = CronExpression("0 0 1 1 *")
        with pytest.raises(ScheduleExhausted):
            schedule.next_after(at(9999, 12, 31, 23, 58))

    def test_overflow_message_names_the_expression(self):
        with pytest.raises(ScheduleExhausted, match="0 0 1 1"):
            CronExpression("0 0 1 1 *").next_after(dt.datetime.max)

    def test_iter_after_near_datetime_max_raises_schedule_exhausted(self):
        with pytest.raises(ScheduleExhausted):
            CronExpression("0 0 1 1 *").iter_after(at(9999, 12, 31, 23, 58), 2)

    def test_impossible_schedule_message_reports_the_horizon(self):
        with pytest.raises(ScheduleExhausted, match="2928 days"):
            CronExpression("0 0 31 2 *").next_after(at(2026, 10, 29))


class TestArgumentValidation:
    def test_iter_after_names_itself_in_the_type_error(self):
        with pytest.raises(TypeError, match="iter_after"):
            CronExpression("* * * * *").iter_after("2026-10-29", 2)

    def test_iter_after_rejects_a_date(self):
        with pytest.raises(TypeError):
            CronExpression("* * * * *").iter_after(dt.date(2026, 10, 29), 2)

    def test_next_after_rejects_a_date(self):
        with pytest.raises(TypeError):
            CronExpression("* * * * *").next_after(dt.date(2026, 10, 29))

    def test_iter_after_rejects_a_bool_count(self):
        # bool is an int subclass, so it needs an explicit check.
        with pytest.raises(TypeError):
            CronExpression("* * * * *").iter_after(at(2026, 10, 29), True)


class TestSearchInvariants:
    """Properties that must hold for every expression, not just the samples."""

    EXPRESSIONS = [
        "* * * * *",
        "*/15 * * * *",
        "0 9-17 * * 1-5",
        "30 6 * * 0",
        "0 0 31 * *",
        "0 0 13 * FRI",
        "5/20 23 31 1,7 5",
        "*/7 */5 15-20 12 0",
        "0 0 29 2 *",
        "@weekly",
    ]

    STARTS = [
        at(2026, 10, 29, 21, 2),
        at(2026, 12, 31, 23, 59),
        at(2028, 2, 29, 0, 0),
        at(2024, 2, 28, 23, 0),
    ]

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_result_matches_and_is_strictly_after(self, expression):
        schedule = CronExpression(expression)
        for start in self.STARTS:
            nxt = schedule.next_after(start)
            assert nxt > start
            assert schedule.matches(nxt)
            assert (nxt.second, nxt.microsecond) == (0, 0)

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_iter_after_equals_repeated_next_after(self, expression):
        schedule = CronExpression(expression)
        start = at(2026, 10, 29, 21, 2)
        walked = schedule.iter_after(start, 4)
        cursor, expected = start, []
        for _ in range(4):
            cursor = schedule.next_after(cursor)
            expected.append(cursor)
        assert walked == tuple(expected)

    @pytest.mark.parametrize("expression", EXPRESSIONS)
    def test_aware_and_naive_agree_on_wall_clock(self, expression):
        schedule = CronExpression(expression)
        naive = schedule.next_after(at(2026, 10, 29, 21, 2))
        tz = dt.timezone(dt.timedelta(hours=5, minutes=30))
        aware = schedule.next_after(at(2026, 10, 29, 21, 2, tzinfo=tz))
        assert aware.replace(tzinfo=None) == naive
        assert aware.tzinfo is tz
