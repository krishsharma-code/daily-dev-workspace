"""Tests for fiscal_year: Indian fiscal-year and quarter arithmetic."""

import datetime as dt
import doctest

import pytest

import fiscal_year
from fiscal_year import (
    MAX_YEAR,
    MIN_YEAR,
    FiscalYearError,
    fy_bounds,
    fy_days,
    fy_label,
    fy_months,
    fy_of,
    fy_quarters,
    fy_span,
    month_of_fy,
    parse_fy,
    quarter_bounds,
    quarter_label,
    quarter_of,
    shift_fy,
)

D = dt.date


class TestFyOf:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (D(2025, 4, 1), 2025),    # first day of FY 2025-26
            (D(2025, 4, 2), 2025),
            (D(2025, 12, 31), 2025),
            (D(2026, 1, 1), 2025),    # calendar year rolled over, FY did not
            (D(2026, 2, 28), 2025),
            (D(2026, 3, 31), 2025),   # last day of FY 2025-26
            (D(2026, 4, 1), 2026),    # next FY begins
            (D(2024, 3, 31), 2023),
            (D(1999, 5, 5), 1999),
        ],
    )
    def test_boundaries(self, day, expected):
        assert fy_of(day) == expected

    def test_accepts_datetime_and_truncates(self):
        assert fy_of(dt.datetime(2026, 3, 31, 23, 59, 59)) == 2025

    @pytest.mark.parametrize("bad", ["2025-05-01", 2025, None, 3.5, True])
    def test_rejects_non_dates(self, bad):
        with pytest.raises(FiscalYearError):
            fy_of(bad)


class TestFyBounds:
    @pytest.mark.parametrize(
        "year, start, end",
        [
            (2025, D(2025, 4, 1), D(2026, 3, 31)),
            (2023, D(2023, 4, 1), D(2024, 3, 31)),
            (1999, D(1999, 4, 1), D(2000, 3, 31)),
        ],
    )
    def test_bounds(self, year, start, end):
        assert fy_bounds(year) == (start, end)

    def test_bounds_round_trip_with_fy_of(self):
        for year in (1000, 2024, 2025, MAX_YEAR):
            start, end = fy_bounds(year)
            assert fy_of(start) == year
            assert fy_of(end) == year

    @pytest.mark.parametrize("bad", [MIN_YEAR - 1, MAX_YEAR + 1, 0, -2025])
    def test_rejects_out_of_range_years(self, bad):
        with pytest.raises(FiscalYearError):
            fy_bounds(bad)

    @pytest.mark.parametrize("bad", [2025.0, "2025", None, True])
    def test_rejects_non_int_years(self, bad):
        with pytest.raises(FiscalYearError):
            fy_bounds(bad)


class TestQuarterOf:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (D(2025, 4, 1), 1),
            (D(2025, 6, 30), 1),
            (D(2025, 7, 1), 2),
            (D(2025, 9, 30), 2),
            (D(2025, 10, 1), 3),
            (D(2025, 12, 31), 3),
            (D(2026, 1, 1), 4),
            (D(2026, 3, 31), 4),
        ],
    )
    def test_quarters(self, day, expected):
        assert quarter_of(day) == expected

    def test_january_is_q4_not_q1(self):
        assert quarter_of(D(2026, 1, 5)) == 4


class TestQuarterBounds:
    @pytest.mark.parametrize(
        "year, quarter, start, end",
        [
            (2025, 1, D(2025, 4, 1), D(2025, 6, 30)),
            (2025, 2, D(2025, 7, 1), D(2025, 9, 30)),
            (2025, 3, D(2025, 10, 1), D(2025, 12, 31)),
            (2025, 4, D(2026, 1, 1), D(2026, 3, 31)),
            (2023, 1, D(2023, 4, 1), D(2023, 6, 30)),
        ],
    )
    def test_bounds(self, year, quarter, start, end):
        assert quarter_bounds(year, quarter) == (start, end)

    def test_every_day_maps_back_to_its_quarter(self):
        for year in (2023, 2024, 2025):
            for quarter in (1, 2, 3, 4):
                start, end = quarter_bounds(year, quarter)
                assert quarter_of(start) == quarter
                assert quarter_of(end) == quarter
                assert fy_of(start) == year
                assert fy_of(end) == year

    def test_quarters_are_contiguous(self):
        quarters = fy_quarters(2025)
        for (_, prev_end), (next_start, _) in zip(quarters, quarters[1:]):
            assert next_start - prev_end == dt.timedelta(days=1)

    @pytest.mark.parametrize("bad", [0, 5, -1, 1.0, "1", True])
    def test_rejects_bad_quarter(self, bad):
        with pytest.raises(FiscalYearError):
            quarter_bounds(2025, bad)


class TestMonthOfFy:
    @pytest.mark.parametrize(
        "day, expected",
        [
            (D(2025, 4, 10), 1),
            (D(2025, 5, 1), 2),
            (D(2025, 12, 31), 9),
            (D(2026, 1, 1), 10),
            (D(2026, 3, 1), 12),
        ],
    )
    def test_month_index(self, day, expected):
        assert month_of_fy(day) == expected

    def test_fy_months_match_month_of_fy(self):
        for index, (year, month) in enumerate(fy_months(2025), start=1):
            assert month_of_fy(D(year, month, 1)) == index


class TestLabels:
    @pytest.mark.parametrize(
        "style, expected",
        [
            ("short", "FY2025-26"),
            ("long", "FY2025-2026"),
            ("plain", "2025-26"),
            ("compact", "FY25"),
        ],
    )
    def test_styles(self, style, expected):
        assert fy_label(2025, style) == expected

    def test_default_style(self):
        assert fy_label(2025) == "FY2025-26"

    def test_century_rollover_pads_end_year(self):
        assert fy_label(2099, "short") == "FY2099-00"

    def test_quarter_label(self):
        assert quarter_label(2025, 4) == "Q4 FY2025-26"
        assert quarter_label(2025, 1, "long") == "Q1 FY2025-2026"

    def test_rejects_unknown_style(self):
        with pytest.raises(FiscalYearError):
            fy_label(2025, "medium")

    def test_rejects_bad_quarter_in_quarter_label(self):
        with pytest.raises(FiscalYearError):
            quarter_label(2025, 0)


class TestParseFy:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("FY2025-26", 2025),
            ("fy2025-26", 2025),
            ("FY 2025-26", 2025),
            ("FY2025-2026", 2025),
            ("2025-26", 2025),
            ("2025-2026", 2025),
            ("2025/26", 2025),
            ("2025/2026", 2025),
            ("FY 2025 / 26", 2025),
            ("2025", 2025),
            ("FY2025", 2025),
            ("FY25", 2025),
            ("FY 25", 2025),
            ("25-26", 2025),
            ("FY25-26", 2025),
            ("FY 2025\u201326", 2025),  # en dash, as pasted from a report
            ("FY 2025\u20142026", 2025),  # em dash
            ("  2025-26  ", 2025),
            (2025, 2025),
            ("2099-00", 2099),
        ],
    )
    def test_accepted_forms(self, text, expected):
        assert parse_fy(text) == expected

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "FY",
            "abc",
            "2025-2027",   # two-year span
            "2025-25",     # end year is not start + 1
            "26-25",       # reversed
            "20255",
            "FY202",
            "2025-26-27",
            "2025.26",
        ],
    )
    def test_rejected_forms(self, bad):
        with pytest.raises(FiscalYearError):
            parse_fy(bad)

    @pytest.mark.parametrize("bad", [None, 2025.0, True, ["2025"]])
    def test_rejects_non_text(self, bad):
        with pytest.raises(FiscalYearError):
            parse_fy(bad)

    def test_round_trip_with_fy_label(self):
        for year in (1999, 2025, 2099):
            for style in ("short", "long", "plain"):
                assert parse_fy(fy_label(year, style)) == year
        for year in (2000, 2025, 2099):
            assert parse_fy(fy_label(year, "compact")) == year

    def test_two_digit_label_reads_as_20xx(self):
        # A compact label has no century in it; 20xx is the documented reading.
        assert parse_fy("FY99") == 2099
        assert parse_fy("99-00") == 2099


class TestShiftFy:
    def test_shift(self):
        assert shift_fy(2025, 1) == 2026
        assert shift_fy(2025, -1) == 2024
        assert shift_fy(2025, 0) == 2025

    def test_rejects_out_of_range_result(self):
        with pytest.raises(FiscalYearError):
            shift_fy(MAX_YEAR, 1)
        with pytest.raises(FiscalYearError):
            shift_fy(MIN_YEAR, -1)

    def test_rejects_non_int_periods(self):
        with pytest.raises(FiscalYearError):
            shift_fy(2025, 1.5)


class TestFyMonthsAndQuarters:
    def test_twelve_ordered_months(self):
        months = fy_months(2025)
        assert len(months) == 12
        assert months[0] == (2025, 4)
        assert months[-1] == (2026, 3)
        assert [month for _, month in months] == [
            4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3
        ]

    def test_four_quarters(self):
        quarters = fy_quarters(2025)
        assert len(quarters) == 4
        assert quarters[0][0] == D(2025, 4, 1)
        assert quarters[-1][1] == D(2026, 3, 31)

    def test_rejects_bad_year(self):
        with pytest.raises(FiscalYearError):
            fy_months("2025")
        with pytest.raises(FiscalYearError):
            fy_quarters(0)


class TestFyDays:
    @pytest.mark.parametrize(
        "year, expected",
        [
            (2023, 366),  # contains 29 Feb 2024
            (2024, 365),
            (2025, 365),
            (2027, 366),  # contains 29 Feb 2028
            (2099, 365),
        ],
    )
    def test_days(self, year, expected):
        assert fy_days(year) == expected

    def test_days_match_bounds(self):
        for year in (2023, 2024, 2025):
            start, end = fy_bounds(year)
            assert fy_days(year) == (end - start).days + 1

    def test_quarters_sum_to_year(self):
        for year in (2023, 2024, 2025):
            total = sum((end - start).days + 1 for start, end in fy_quarters(year))
            assert total == fy_days(year)


class TestFySpan:
    def test_single_year(self):
        assert fy_span(D(2025, 5, 1), D(2026, 2, 1)) == (2025,)

    def test_crossing_boundary(self):
        assert fy_span(D(2025, 12, 1), D(2026, 5, 1)) == (2025, 2026)
        assert fy_span(D(2024, 3, 31), D(2024, 4, 1)) == (2023, 2024)

    def test_same_day(self):
        assert fy_span(D(2025, 6, 1), D(2025, 6, 1)) == (2025,)

    def test_rejects_reversed_range(self):
        with pytest.raises(FiscalYearError):
            fy_span(D(2026, 1, 1), D(2025, 1, 1))

    def test_rejects_non_dates(self):
        with pytest.raises(FiscalYearError):
            fy_span("2025-01-01", D(2025, 6, 1))


def test_public_api_is_declared():
    for name in fiscal_year.__all__:
        assert hasattr(fiscal_year, name), name


def test_docstring_examples_still_hold():
    failures, _ = doctest.testmod(fiscal_year)
    assert failures == 0
