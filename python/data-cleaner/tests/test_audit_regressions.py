"""Regression tests from the data-cleaner audit (audit/review-datacleaner).

Each test here pins one defect that the review found and the fix closed, so the
behaviour cannot quietly regress:

1. a one-shot iterable of ``missing_tokens`` was drained by the first field, so
   every later field compared against an empty set;
2. the token set was rebuilt for every string cell of every record;
3. ``dedupe_records`` raised ``TypeError`` on a ``bytearray``/``memoryview``
   even though its docstring promises unhashable values are fine;
4. ``normalize_phone`` stripped a leading minus from a *string* and accepted a
   negative number that the ``int`` branch rejected;
5. ``to_number`` accepted a number with mixed thousands separators
   (``"1,234 567"``) and silently coerced it to ``1234567``.
"""

from __future__ import annotations

import pytest

from datacleaner import (
    clean_record,
    clean_records,
    dedupe_records,
    is_missing,
    missing_required,
    normalize_phone,
    to_number,
)


class TestOneShotMissingTokens:
    """``missing_tokens`` is typed ``Iterable[str]``, so a generator must work."""

    def test_generator_applies_to_every_field_of_one_record(self):
        tokens = (token for token in {"n/a", "none"})
        cleaned = clean_record(
            {"a": "n/a", "b": "n/a", "c": "none"}, missing_tokens=tokens
        )
        assert cleaned == {"a": None, "b": None, "c": None}

    def test_generator_applies_to_every_record_in_a_batch(self):
        tokens = iter(["n/a"])
        cleaned = clean_records(
            [{"a": "n/a"}, {"a": "n/a"}, {"a": "kept"}],
            missing_tokens=tokens,
            drop_empty=False,
        )
        assert cleaned == [{"a": None}, {"a": None}, {"a": "kept"}]

    def test_generator_applies_to_every_required_field(self):
        tokens = (token for token in {"n/a"})
        missing = missing_required(
            {"a": "n/a", "b": "n/a", "c": "1"}, ["a", "b", "c"], missing_tokens=tokens
        )
        assert missing == ["a", "b"]

    def test_tokens_are_casefolded_even_when_a_frozenset_is_passed(self):
        # Guards the idempotence fast path: only sets *we* normalised may be
        # returned unchanged, never an arbitrary frozenset from the caller.
        assert is_missing("n/a", frozenset({"N/A"})) is True
        assert is_missing("N/A", frozenset({"n/a"})) is True

    def test_normalised_tokens_are_reusable(self):
        tokens = frozenset({"N/A"})
        assert [is_missing("n/a", tokens) for _ in range(3)] == [True, True, True]

    def test_default_tokens_still_match(self):
        assert clean_record({"a": " unknown "}) == {"a": None}


class TestBinaryValuesDedupe:
    """``dedupe_records`` promises unhashable values are handled."""

    def test_bytearray_does_not_raise(self):
        records = [{"payload": bytearray(b"ab")}, {"payload": bytearray(b"ab")}]
        assert dedupe_records(records) == [{"payload": bytearray(b"ab")}]

    def test_memoryview_does_not_raise(self):
        records = [{"payload": memoryview(b"ab")}, {"payload": memoryview(b"ab")}]
        assert len(dedupe_records(records)) == 1

    def test_distinct_byte_payloads_are_kept(self):
        records = [{"payload": bytearray(b"ab")}, {"payload": bytearray(b"cd")}]
        assert len(dedupe_records(records)) == 2

    def test_bytearray_and_bytes_with_same_content_compare_equal(self):
        # Freezing to ``bytes`` means the two spellings of the same payload are
        # one record, which is what a caller comparing a JSON round-trip wants.
        assert len(dedupe_records([{"p": bytearray(b"ab")}, {"p": b"ab"}])) == 1


class TestNegativePhoneNumbers:
    """A sign is not a separator; the int and str branches must agree."""

    @pytest.mark.parametrize("value", ["-9876543210", " -9876543210 ", -9876543210])
    def test_negative_is_rejected(self, value):
        assert normalize_phone(value) is None

    @pytest.mark.parametrize(
        "value",
        ["98765-43210", "98765 43210", "(98765) 43210", "+91 98765-43210"],
    )
    def test_interior_separators_still_accepted(self, value):
        assert normalize_phone(value) == "+919876543210"


class TestMixedGroupSeparators:
    """A number may use commas or spaces, not both."""

    @pytest.mark.parametrize(
        "value", ["1,234 567", "1 234,567", "1,234 567,890", "₹1,23 456"]
    )
    def test_mixed_separators_are_rejected(self, value):
        assert to_number(value) is None

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("1,234", 1234),
            ("1,234,567", 1234567),
            ("1 234", 1234),
            ("1 234 567", 1234567),
            ("1,234.50", 1234.5),
            ("₹1,200", 1200),
            ("-1,234", -1234),
        ],
    )
    def test_homogeneous_separators_still_accepted(self, value, expected):
        assert to_number(value) == expected
