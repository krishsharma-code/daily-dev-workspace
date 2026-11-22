"""Tests for inr_money: Indian grouping, formatting, parsing and words."""

from decimal import Decimal

import pytest

import inr_money
from inr_money import (
    MAX_DECIMALS,
    MoneyError,
    amount_to_words,
    format_inr,
    group_digits,
    integer_to_words,
    parse_inr,
)


class TestGroupDigits:
    @pytest.mark.parametrize(
        "digits, expected",
        [
            ("0", "0"),
            ("5", "5"),
            ("12", "12"),
            ("123", "123"),
            ("1234", "1,234"),
            ("12345", "12,345"),
            ("123456", "1,23,456"),
            ("1234567", "12,34,567"),
            ("12345678", "1,23,45,678"),
            ("10000000", "1,00,00,000"),
            ("100000000", "10,00,00,000"),
            ("999999999999", "9,99,99,99,99,999"),
            ("1000000000000", "10,00,00,00,00,000"),
        ],
    )
    def test_grouping(self, digits, expected):
        assert group_digits(digits) == expected

    def test_single_digit_group_boundary(self):
        # The only place a one-digit group is legal is the head of a long run.
        assert group_digits("1234567890123") == "12,34,56,78,90,123"

    @pytest.mark.parametrize("bad", ["", "12a4", "1,234", "-123", " 123"])
    def test_rejects_non_digits(self, bad):
        with pytest.raises(MoneyError):
            group_digits(bad)

    def test_rejects_non_string(self):
        with pytest.raises(MoneyError):
            group_digits(1234567)


class TestFormatInr:
    @pytest.mark.parametrize(
        "amount, expected",
        [
            (0, "\u20b90.00"),
            (1, "\u20b91.00"),
            (999, "\u20b9999.00"),
            (1000, "\u20b91,000.00"),
            (99999, "\u20b999,999.00"),
            (100000, "\u20b91,00,000.00"),
            (1234567, "\u20b912,34,567.00"),
            (12345678.9, "\u20b91,23,45,678.90"),
            (10000000, "\u20b91,00,00,000.00"),
            (1234567890123, "\u20b912,34,56,78,90,123.00"),
        ],
    )
    def test_indian_grouping(self, amount, expected):
        assert format_inr(amount) == expected

    def test_keeps_paise(self):
        assert format_inr(Decimal("1234.56")) == "\u20b91,234.56"
        assert format_inr(Decimal("0.05")) == "\u20b90.05"

    def test_zero_decimals(self):
        assert format_inr(1234567, decimals=0) == "\u20b912,34,567"

    def test_rounds_half_up_not_half_even(self):
        # Python's round() would give 1234 for both of the first two.
        assert format_inr(1234.5, decimals=0) == "\u20b91,235"
        assert format_inr(1235.5, decimals=0) == "\u20b91,236"
        assert format_inr(Decimal("0.005")) == "\u20b90.01"
        assert format_inr(Decimal("0.015")) == "\u20b90.02"

    def test_rounding_carries_into_grouping(self):
        assert format_inr(Decimal("99999.999")) == "\u20b91,00,000.00"

    def test_float_goes_through_str(self):
        # 0.1 + 0.2 is 0.30000000000000004 as a float.
        assert format_inr(0.1 + 0.2) == "\u20b90.30"
        assert format_inr(1.005) == "\u20b91.01"

    def test_negative_zero_never_printed(self):
        assert format_inr(Decimal("-0.004")) == "\u20b90.00"
        assert format_inr(Decimal("-0.00")) == "\u20b90.00"

    def test_negative(self):
        assert format_inr(-1234) == "-\u20b91,234.00"

    def test_accounting_negative(self):
        assert format_inr(-1234, accounting=True) == "(\u20b91,234.00)"

    def test_accounting_leaves_positives_alone(self):
        assert format_inr(1234, accounting=True) == "\u20b91,234.00"

    def test_symbol_options(self):
        assert format_inr(1234, symbol=None) == "1,234.00"
        assert format_inr(1234, symbol="") == "1,234.00"
        assert format_inr(1234, symbol="INR") == "INR1,234.00"
        assert format_inr(1234, symbol="INR", space=True) == "INR 1,234.00"
        assert format_inr(1234, space=True) == "\u20b9 1,234.00"

    def test_accepts_string_amount(self):
        assert format_inr("\u20b9 1,23,456.78") == "\u20b91,23,456.78"
        assert format_inr("2 lakh") == "\u20b92,00,000.00"

    def test_accepts_decimal_scale(self):
        assert format_inr(Decimal("1234.5"), decimals=4) == "\u20b91,234.5000"

    def test_round_trip_through_parse(self):
        for amount in (0, 1, 999, 1234567, 12345678.9, -4321.05):
            rendered = format_inr(amount)
            assert parse_inr(rendered) == parse_inr(str(amount))

    @pytest.mark.parametrize("decimals", [-1, MAX_DECIMALS + 1, 1.5, "2", True])
    def test_rejects_bad_decimals(self, decimals):
        with pytest.raises(MoneyError):
            format_inr(100, decimals=decimals)

    def test_rejects_bad_symbol(self):
        with pytest.raises(MoneyError):
            format_inr(100, symbol=1)

    @pytest.mark.parametrize("bad", [True, None, [1], {"a": 1}, object()])
    def test_rejects_bad_amount_types(self, bad):
        with pytest.raises(MoneyError):
            format_inr(bad)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite_floats(self, bad):
        with pytest.raises(MoneyError):
            format_inr(bad)


class TestParseInr:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("0", Decimal("0")),
            ("1234", Decimal("1234")),
            ("\u20b91,23,456.78", Decimal("123456.78")),
            ("\u20b9 1,23,456", Decimal("123456")),
            ("Rs. 1,23,456", Decimal("123456")),
            ("Rs 4500", Decimal("4500")),
            ("rs.4500", Decimal("4500")),
            ("INR 500", Decimal("500")),
            ("inr 1,00,000.50", Decimal("100000.50")),
            ("123,456.78", Decimal("123456.78")),
            ("1 23 456", Decimal("123456")),
            ("  999  ", Decimal("999")),
            (".5", Decimal("0.5")),
            ("+250", Decimal("250")),
            ("-250", Decimal("-250")),
        ],
    )
    def test_plain_amounts(self, text, expected):
        assert parse_inr(text) == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("(1,234.50)", Decimal("-1234.50")),
            ("(\u20b91,234.50)", Decimal("-1234.50")),
            ("(-\u20b91,234.50)", Decimal("1234.50")),
            ("-(\u20b91,234)", Decimal("1234")),
        ],
    )
    def test_parentheses_and_signs(self, text, expected):
        assert parse_inr(text) == expected

    @pytest.mark.parametrize(
        "text, expected",
        [
            ("2.5 lakh", Decimal("250000")),
            ("2.5 Lakhs", Decimal("250000")),
            ("3 lacs", Decimal("300000")),
            ("1 crore", Decimal("10000000")),
            ("1.2 cr", Decimal("12000000")),
            ("1.2 CRORES", Decimal("12000000")),
            ("5 thousand", Decimal("5000")),
            ("12k", Decimal("12000")),
            ("12K", Decimal("12000")),
            ("\u20b92.5 lakh", Decimal("250000")),
            ("-2.5 lakh", Decimal("-250000")),
        ],
    )
    def test_multiplier_suffixes(self, text, expected):
        assert parse_inr(text) == expected

    def test_lakh_is_not_read_as_kilo(self):
        # 'lakh' ends in 'k'; the suffix table must prefer the longer match.
        assert parse_inr("1 lakh") == Decimal("100000")
        assert parse_inr("1k") == Decimal("1000")

    def test_multiplier_result_has_no_trailing_scale(self):
        assert str(parse_inr("2.5 lakh")) == "250000"
        assert str(parse_inr("1.20 cr")) == "12000000"

    def test_plain_input_keeps_the_scale_it_was_given(self):
        assert str(parse_inr("1.50")) == "1.50"

    def test_accepts_numbers_directly(self):
        assert parse_inr(250) == Decimal("250")
        assert parse_inr(Decimal("250.75")) == Decimal("250.75")
        assert parse_inr(0.5) == Decimal("0.5")

    @pytest.mark.parametrize(
        "bad",
        [
            "",
            "   ",
            "abc",
            "\u20b9",
            "Rs.",
            "1.2.3",
            "1,2,3.4.5",
            "12 rupees",
            "1 lakh crore",
            "1e5",
            "()",
            "(",
            "-",
            "1..2",
        ],
    )
    def test_rejects_garbage(self, bad):
        with pytest.raises(MoneyError):
            parse_inr(bad)

    @pytest.mark.parametrize("bad", [None, True, False, [1], {"a": 1}])
    def test_rejects_bad_types(self, bad):
        with pytest.raises(MoneyError):
            parse_inr(bad)

    @pytest.mark.parametrize("bad", [Decimal("NaN"), Decimal("Infinity")])
    def test_rejects_non_finite_decimals(self, bad):
        with pytest.raises(MoneyError):
            parse_inr(bad)


class TestIntegerToWords:
    @pytest.mark.parametrize(
        "number, expected",
        [
            (0, "Zero"),
            (1, "One"),
            (10, "Ten"),
            (13, "Thirteen"),
            (20, "Twenty"),
            (21, "Twenty-One"),
            (99, "Ninety-Nine"),
            (100, "One Hundred"),
            (101, "One Hundred One"),
            (110, "One Hundred Ten"),
            (999, "Nine Hundred Ninety-Nine"),
            (1000, "One Thousand"),
            (1001, "One Thousand One"),
            (12345, "Twelve Thousand Three Hundred Forty-Five"),
            (100000, "One Lakh"),
            (123456, "One Lakh Twenty-Three Thousand Four Hundred Fifty-Six"),
            (1000000, "Ten Lakh"),
            (1234567, "Twelve Lakh Thirty-Four Thousand Five Hundred Sixty-Seven"),
            (10000000, "One Crore"),
            (12345678, "One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred Seventy-Eight"),
            (100000000, "Ten Crore"),
            (1000000000, "One Hundred Crore"),
        ],
    )
    def test_words(self, number, expected):
        assert integer_to_words(number) == expected

    def test_uses_indian_scale_not_millions(self):
        words = integer_to_words(1234567890)
        assert "Crore" in words and "Lakh" in words
        assert "Billion" not in words and "Million" not in words

    def test_large_number_recurses_over_crore(self):
        assert integer_to_words(10**12) == "One Lakh Crore"

    @pytest.mark.parametrize("bad", [-1, -1000, 1.5, "10", True, None])
    def test_rejects_invalid(self, bad):
        with pytest.raises(MoneyError):
            integer_to_words(bad)


class TestAmountToWords:
    @pytest.mark.parametrize(
        "amount, expected",
        [
            (0, "Zero Rupees Only"),
            (1, "One Rupee Only"),
            (2, "Two Rupees Only"),
            (100, "One Hundred Rupees Only"),
            (1234, "One Thousand Two Hundred Thirty-Four Rupees Only"),
            (
                1234.5,
                "One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise Only",
            ),
            (
                12345678.9,
                "One Crore Twenty-Three Lakh Forty-Five Thousand Six Hundred "
                "Seventy-Eight Rupees and Ninety Paise Only",
            ),
            (0.01, "Zero Rupees and One Paise Only"),
        ],
    )
    def test_words(self, amount, expected):
        assert amount_to_words(amount) == expected

    def test_rounds_to_paise_half_up(self):
        assert amount_to_words(Decimal("1.005")) == "One Rupee and One Paise Only"
        assert amount_to_words(Decimal("1.004")) == "One Rupee Only"

    def test_negative(self):
        assert amount_to_words(-1) == "Minus One Rupee Only"

    def test_negative_zero_has_no_minus(self):
        assert amount_to_words(Decimal("-0.001")) == "Zero Rupees Only"

    def test_options(self):
        assert amount_to_words(1234.5, include_paise=False) == (
            "One Thousand Two Hundred Thirty-Four Rupees Only"
        )
        assert amount_to_words(1234.5, include_only=False) == (
            "One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise"
        )
        assert amount_to_words(1234, rupees="Rupaya") == "One Thousand Two Hundred Thirty-Four Rupayas Only"
        # "paise" is invariant by default: no trailing "s".
        assert amount_to_words(1.5, paise="paisa") == "One Rupee and Fifty paisa Only"
        assert amount_to_words(1.5, paise="paisa", paise_plural="paise") == (
            "One Rupee and Fifty paise Only"
        )
        assert amount_to_words(2, rupees="Rupaya", rupees_plural="Rupaye") == (
            "Two Rupaye Only"
        )

    def test_accepts_parsed_text(self):
        assert amount_to_words("\u20b91,000") == "One Thousand Rupees Only"

    @pytest.mark.parametrize("bad", ["", "  ", 1])
    def test_rejects_bad_nouns(self, bad):
        with pytest.raises(MoneyError):
            amount_to_words(100, rupees=bad)

    @pytest.mark.parametrize("bad", ["", None, 1])
    def test_rejects_bad_paise_noun(self, bad):
        with pytest.raises(MoneyError):
            amount_to_words(100, paise=bad)


class TestModuleContract:
    def test_error_is_a_value_error(self):
        assert issubclass(MoneyError, ValueError)

    def test_public_api_is_exported(self):
        assert set(inr_money.__all__) == {
            "MoneyError",
            "amount_to_words",
            "format_inr",
            "group_digits",
            "integer_to_words",
            "parse_inr",
        }
        for name in inr_money.__all__:
            assert hasattr(inr_money, name)

    def test_no_float_arithmetic_in_implementation(self):
        # Money must never be computed in binary floating point.
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(inr_money))
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                assert not isinstance(node.op, ast.Div), "use Decimal, not true division"

    def test_docstring_examples_are_accurate(self):
        assert format_inr(12345678.9) == "\u20b91,23,45,678.90"
        assert format_inr(-1234, accounting=True) == "(\u20b91,234.00)"
        assert parse_inr("2.5 lakh") == Decimal("250000")
        assert amount_to_words(1234.5) == (
            "One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise Only"
        )
