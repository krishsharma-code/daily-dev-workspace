"""Regression tests for the edge cases found in the 2026-11-06 code review.

Each class pins one finding from that review so the defect cannot come back:
the frozen-set dedupe bug, the one-way case sensitivity in ``missing_tokens``,
the "strip separators anywhere" numeric coercion, the unchecked
``country_code``, the nan/inf disagreement between ``to_number`` and
``is_missing``, and the non-callable transformer guard.

Run from ``python/data-cleaner``::

    python -m pytest tests/test_robustness.py
"""

import math

import pytest

import datacleaner
from datacleaner import (
    clean_record,
    clean_records,
    dedupe_records,
    is_missing,
    missing_required,
    normalize_phone,
    to_number,
)


class TestDedupeWithSets:
    """``_freeze`` must be order-independent for sets, or equal rows survive."""

    def test_equal_sets_freeze_identically(self):
        # Built in opposite insertion orders: equal sets, and the freeze used
        # for comparison must agree or dedupe keeps both rows.
        first = {"aa", "bb", "cc", "dd", "ee", "ff"}
        second = set()
        for word in ["ff", "ee", "dd", "cc", "bb", "aa"]:
            second.add(word)

        assert first == second
        assert datacleaner._freeze(first) == datacleaner._freeze(second)

    def test_equal_sets_are_deduped(self):
        records = [
            {"tags": {"red", "green", "blue"}},
            {"tags": {"blue", "red", "green"}},
        ]
        assert len(dedupe_records(records)) == 1

    def test_set_and_frozenset_are_deduped(self):
        records = [{"tags": {"x", "y"}}, {"tags": frozenset({"y", "x"})}]
        assert len(dedupe_records(records)) == 1

    def test_different_sets_are_kept(self):
        records = [{"tags": {"a"}}, {"tags": {"b"}}]
        assert len(dedupe_records(records)) == 2

    def test_mixed_type_set_does_not_raise(self):
        # Sorting by ``repr`` keeps this working where a raw ``sorted`` would
        # raise TypeError on int vs str.
        records = [{"tags": {1, "a"}}, {"tags": {"a", 1}}]
        assert len(dedupe_records(records)) == 1

    def test_list_order_still_matters(self):
        # Sequences are ordered, so these must stay distinct.
        records = [{"tags": ["a", "b"]}, {"tags": ["b", "a"]}]
        assert len(dedupe_records(records)) == 2


class TestMissingTokenNormalisation:
    """A caller's tokens are compared the same way the value is."""

    @pytest.mark.parametrize("token", ["N/A", "n/a", " N/A ", "N/a"])
    def test_uppercase_custom_tokens_match(self, token):
        assert is_missing("n/a", missing_tokens={token}) is True

    def test_custom_token_whitespace_is_normalised(self):
        assert is_missing("  not   set ", missing_tokens={"not set"}) is True

    def test_missing_required_honours_custom_tokens(self):
        record = {"name": "N/A", "email": "a@b.co"}
        assert missing_required(record, ["name", "email"], missing_tokens={"N/A"}) == [
            "name"
        ]

    def test_non_string_tokens_are_ignored(self):
        # A non-string token can never equal a string value; it must not raise.
        assert is_missing("x", missing_tokens={None, 1, "x"}) is True

    def test_default_tokens_unchanged(self):
        assert is_missing("N/A") is True
        assert is_missing("nope") is False


class TestNonFiniteIsMissing:
    """``to_number`` maps nan/inf to None, so ``is_missing`` must agree."""

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_float_is_missing(self, value):
        assert is_missing(value) is True

    def test_clean_record_nulls_non_finite(self):
        assert clean_record({"v": float("nan")}) == {"v": None}

    def test_missing_required_flags_non_finite(self):
        assert missing_required({"v": float("inf")}, ["v"]) == ["v"]

    def test_finite_floats_stay_present(self):
        assert is_missing(0.0) is False
        assert is_missing(1.5) is False

    def test_bool_is_not_a_float(self):
        assert is_missing(False) is False
        assert math.isfinite(1.0)


class TestNumberGrammar:
    """Separators are only accepted where a real number may contain them."""

    @pytest.mark.parametrize(
        "value",
        [
            "12$34",  # symbol in the middle
            "1$234",
            "1,2,3",  # not groups of three
            "12,34",
            "1,,234",
            "4 5 6",
            "1_000",  # float() accepts underscores; a data cell should not
            "1_0",
            "1.2.3",
            "--5",
            "5-",
            "\u20b9",  # currency symbol alone
            "1,234abc",
        ],
    )
    def test_malformed_numbers_are_rejected(self, value):
        assert to_number(value) is None

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1,234", 1234),
            ("12,34,567", None),
            ("1,234,567", 1234567),
            ("1 234", 1234),
            ("\u20b91,200", 1200),
            ("$3.50", 3.5),
            ("-7", -7),
            ("+7", 7),
            (".5", 0.5),
            ("1e3", 1000),
            ("1,234.50", 1234.5),
            (" 42 ", 42),
        ],
    )
    def test_well_formed_numbers_still_parse(self, value, expected):
        assert to_number(value) == expected

    def test_overflow_still_rejected(self):
        assert to_number("9" * 400) is None

    def test_float_underscore_string_rejected(self):
        # Guard the exact reason the grammar exists: float("1_0") == 10.0.
        assert float("1_0") == 10.0
        assert to_number("1_0") is None


class TestCountryCodeValidation:
    """A malformed country code is an argument error, not bad data."""

    @pytest.mark.parametrize("code", ["999999", "abc", "", "  ", None, "91x"])
    def test_invalid_country_code_raises(self, code):
        with pytest.raises(ValueError, match="country_code"):
            normalize_phone("9876543210", country_code=code)

    @pytest.mark.parametrize("code", ["91", "1", "44", "971", 91, " 91 "])
    def test_valid_country_code_accepted(self, code):
        result = normalize_phone("9876543210", country_code=code)
        assert result is not None and result.startswith("+")

    def test_invalid_number_still_returns_none(self):
        # Bad *data* stays a None; only bad arguments raise.
        assert normalize_phone("12345") is None
        assert normalize_phone(None) is None
        assert normalize_phone(9876543210.0) is None


class TestTransformerGuard:
    """A non-callable transformer fails up front, naming the field."""

    def test_non_callable_transformer_raises_type_error(self):
        with pytest.raises(TypeError, match="'phone'"):
            clean_record({"phone": "9876543210"}, transformers={"phone": "oops"})

    def test_unused_non_callable_transformer_still_rejected(self):
        # Caught at the door: a typo in the pipeline config fails even for a
        # record that does not carry the field.
        with pytest.raises(TypeError):
            clean_record({"name": "Ada"}, transformers={"phone": 42})

    def test_transformer_exception_propagates(self):
        def boom(value):
            raise RuntimeError("transformer failed")

        with pytest.raises(RuntimeError, match="transformer failed"):
            clean_record({"v": "x"}, transformers={"v": boom})

    def test_clean_records_does_not_swallow_transformer_errors(self):
        def boom(value):
            raise RuntimeError("transformer failed")

        with pytest.raises(RuntimeError):
            clean_records([{"v": "x"}], transformers={"v": boom})


class TestImmutability:
    """The whole module is documented as pure; pin that."""

    def test_clean_record_does_not_mutate_input(self):
        raw = {"name": "  Ada  ", "tags": {"a", "b"}}
        snapshot = dict(raw)
        clean_record(raw, transformers={"name": str.strip})
        assert raw == snapshot

    def test_dedupe_returns_shallow_copies(self):
        raw = [{"a": 1}]
        out = dedupe_records(raw)
        assert out[0] == raw[0]
        assert out[0] is not raw[0]
