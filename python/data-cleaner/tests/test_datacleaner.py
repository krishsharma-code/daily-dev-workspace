"""Tests for the datacleaner toolkit."""

import pytest

from datacleaner import (
    DEFAULT_MISSING_TOKENS,
    clean_record,
    clean_records,
    dedupe_records,
    is_missing,
    missing_required,
    normalize_email,
    normalize_phone,
    normalize_whitespace,
    to_number,
)


class TestNormalizeWhitespace:
    def test_collapses_and_trims(self):
        assert normalize_whitespace("  a\t b\n c  ") == "a b c"

    def test_empty_string(self):
        assert normalize_whitespace("   ") == ""

    def test_non_string_passthrough(self):
        assert normalize_whitespace(42) == 42
        assert normalize_whitespace(None) is None


class TestIsMissing:
    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "NA", "n/a", " N/A ", "None", "null", "nil", "nan",
         "-", "--", "unknown", "UNKNOWN"],
    )
    def test_missing_values(self, value):
        assert is_missing(value) is True

    @pytest.mark.parametrize("value", ["0", "no", "x", 0, 0.0, False, [], {}])
    def test_present_values(self, value):
        assert is_missing(value) is False

    def test_custom_tokens(self):
        assert is_missing("nope", missing_tokens={"nope"}) is True
        assert is_missing("NA", missing_tokens={"nope"}) is False

    def test_default_tokens_are_frozen(self):
        assert isinstance(DEFAULT_MISSING_TOKENS, frozenset)


class TestToNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (5, 5),
            (5.5, 5.5),
            ("42", 42),
            ("12.0", 12),
            ("1,234", 1234),
            ("1,234.50", 1234.5),
            ("\u20b91,200", 1200),
            ("$3.50", 3.5),
            ("1e3", 1000),
            ("-7", -7),
        ],
    )
    def test_numeric(self, value, expected):
        result = to_number(value)
        assert result == expected
        assert isinstance(result, type(expected))

    @pytest.mark.parametrize("value", ["abc", "", "  ", None, True, False, [], {}])
    def test_non_numeric(self, value):
        assert to_number(value) is None


class TestNormalizePhone:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("9876543210", "+919876543210"),
            ("09876543210", "+919876543210"),
            ("+91 98765 43210", "+919876543210"),
            ("+91-98765-43210", "+919876543210"),
            ("919876543210", "+919876543210"),
            ("(98765) 43210", "+919876543210"),
            (9876543210, "+919876543210"),
        ],
    )
    def test_valid(self, value, expected):
        assert normalize_phone(value) == expected

    @pytest.mark.parametrize("value", ["12345", "12345678901234", "", "   ", None, True])
    def test_invalid(self, value):
        assert normalize_phone(value) is None

    def test_custom_country_code(self):
        assert normalize_phone("4155552671", country_code="1") == "+14155552671"
        assert normalize_phone("+1 415 555 2671", country_code="1") == "+14155552671"


class TestNormalizeEmail:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("  Foo.Bar@Example.COM ", "foo.bar@example.com"),
            ("a@b.co", "a@b.co"),
        ],
    )
    def test_valid(self, value, expected):
        assert normalize_email(value) == expected

    @pytest.mark.parametrize("value", ["no-at-sign", "a@b", "a b@c.com", "", None, 42])
    def test_invalid(self, value):
        assert normalize_email(value) is None


class TestCleanRecord:
    def test_whitespace_and_missing(self):
        record = {"name": "  Ada  Lovelace ", "email": "N/A", "age": " 36 "}
        cleaned = clean_record(record)
        assert cleaned == {"name": "Ada Lovelace", "email": None, "age": "36"}

    def test_does_not_mutate_input(self):
        record = {"name": "  Ada  "}
        clean_record(record)
        assert record == {"name": "  Ada  "}

    def test_transformers_applied_to_present_values_only(self):
        record = {"phone": "98765 43210", "backup": "n/a"}
        cleaned = clean_record(
            record, transformers={"phone": normalize_phone, "backup": normalize_phone}
        )
        assert cleaned == {"phone": "+919876543210", "backup": None}

    def test_transformers_combined(self):
        record = {"email": " A@B.COM ", "amount": "1,000"}
        cleaned = clean_record(
            record, transformers={"email": normalize_email, "amount": to_number}
        )
        assert cleaned == {"email": "a@b.com", "amount": 1000}


class TestCleanRecords:
    def test_drops_empty_by_default(self):
        records = [
            {"a": "1", "b": "x"},
            {"a": "N/A", "b": "  "},
            {"a": "2", "b": "y"},
        ]
        assert clean_records(records) == [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}]

    def test_keep_empty_when_disabled(self):
        records = [{"a": "", "b": ""}]
        assert clean_records(records, drop_empty=False) == [{"a": None, "b": None}]

    def test_preserves_order(self):
        records = [{"n": str(i)} for i in range(5)]
        assert [r["n"] for r in clean_records(records)] == ["0", "1", "2", "3", "4"]


class TestDedupeRecords:
    def test_whole_record(self):
        records = [{"a": 1}, {"a": 1}, {"a": 2}]
        assert dedupe_records(records) == [{"a": 1}, {"a": 2}]

    def test_by_key_fields_keeps_first(self):
        records = [
            {"id": 1, "name": "first"},
            {"id": 1, "name": "second"},
            {"id": 2, "name": "third"},
        ]
        result = dedupe_records(records, key_fields=["id"])
        assert result == [{"id": 1, "name": "first"}, {"id": 2, "name": "third"}]

    def test_records_without_key_field_collapse(self):
        # Both lack "id", so both key to None and collapse to one record.
        records = [{"other": 2}, {"another": 3}]
        assert dedupe_records(records, key_fields=["id"]) == [{"other": 2}]

    def test_handles_unhashable_values(self):
        records = [{"tags": ["a", "b"]}, {"tags": ["a", "b"]}, {"tags": ["c"]}]
        assert dedupe_records(records) == [{"tags": ["a", "b"]}, {"tags": ["c"]}]


class TestMissingRequired:
    def test_reports_missing(self):
        record = {"name": "Ada", "email": "N/A"}
        assert missing_required(record, ["name", "email", "phone"]) == ["email", "phone"]

    def test_all_present(self):
        assert missing_required({"a": 1, "b": "x"}, ["a", "b"]) == []
