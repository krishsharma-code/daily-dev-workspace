"""Tests for the Webhook Inbox storage layer (``server/store.py``)."""

import pytest

from store import (
    MAX_BODY_BYTES,
    Event,
    InboxStore,
    ValidationError,
    normalise_bin_name,
)


@pytest.fixture()
def store():
    s = InboxStore(":memory:")
    yield s
    s.close()


class TestBinNames:
    def test_accepts_simple_name(self):
        assert normalise_bin_name("stripe") == "stripe"

    def test_trims_and_lowercases(self):
        assert normalise_bin_name("  Stripe_Test  ") == "stripe_test"

    def test_accepts_dash_and_digits(self):
        assert normalise_bin_name("gh-actions-2") == "gh-actions-2"

    @pytest.mark.parametrize("bad", ["", "   ", "-lead", "_lead", "has space", "a/b", "ünicode"])
    def test_rejects_bad_names(self, bad):
        with pytest.raises(ValidationError):
            normalise_bin_name(bad)

    def test_rejects_too_long(self):
        with pytest.raises(ValidationError):
            normalise_bin_name("a" * 65)

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError):
            normalise_bin_name(None)


class TestBins:
    def test_ensure_bin_reports_created_once(self, store):
        info, created = store.ensure_bin("hooks")
        assert created is True
        assert info["name"] == "hooks"

        info, created = store.ensure_bin("hooks")
        assert created is False
        assert info["name"] == "hooks"

    def test_list_bins_empty(self, store):
        assert store.list_bins() == []

    def test_list_bins_reports_counts_and_last_event(self, store):
        store.add_event("alpha", "POST", "/in/alpha", {}, b"one")
        store.add_event("alpha", "POST", "/in/alpha", {}, b"two")
        store.ensure_bin("beta")

        bins = {b["name"]: b for b in store.list_bins()}
        assert set(bins) == {"alpha", "beta"}
        assert bins["alpha"]["events"] == 2
        assert bins["alpha"]["last_received_at"]
        assert bins["beta"]["events"] == 0
        assert bins["beta"]["last_received_at"] is None

    def test_delete_bin_removes_its_events(self, store):
        store.add_event("alpha", "POST", "/in/alpha", {}, b"x")
        assert store.delete_bin("alpha") is True
        assert store.count_events() == 0
        assert store.count_bins() == 0
        assert store.delete_bin("alpha") is False

    def test_clear_bin_keeps_bin(self, store):
        store.add_event("alpha", "POST", "/in/alpha", {}, b"x")
        store.add_event("alpha", "POST", "/in/alpha", {}, b"y")
        assert store.clear_bin("alpha") == 2
        assert store.count_events("alpha") == 0
        assert store.count_bins() == 1


class TestAddEvent:
    def test_auto_creates_bin(self, store):
        event = store.add_event("newbin", "post", "/in/newbin", {}, b"hi")
        assert isinstance(event, Event)
        assert event.bin == "newbin"
        assert event.method == "POST"  # normalised to uppercase
        assert event.body == b"hi"
        assert event.body_size == 2
        assert event.truncated is False
        assert [b["name"] for b in store.list_bins()] == ["newbin"]

    def test_lowercases_header_names(self, store):
        event = store.add_event(
            "alpha",
            "POST",
            "/in/alpha",
            {"Content-Type": "application/json", "X-Signature": 123},
            b"{}",
        )
        assert event.headers == {"content-type": "application/json", "x-signature": "123"}
        assert event.content_type == "application/json"

    def test_joins_repeated_header_values(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {"Accept": ["a", "b"]})
        assert event.headers["accept"] == "a, b"

    def test_truncates_oversized_body(self, store):
        payload = b"x" * (MAX_BODY_BYTES + 500)
        event = store.add_event("alpha", "POST", "/in/alpha", {}, payload)
        assert event.truncated is True
        assert event.body_size == MAX_BODY_BYTES + 500
        assert len(event.body) == MAX_BODY_BYTES

    def test_accepts_text_body_defensively(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {}, "text")
        assert event.body == b"text"

    def test_rejects_invalid_bin(self, store):
        with pytest.raises(ValidationError):
            store.add_event("bad name", "POST", "/in/bad name")

    def test_binary_body_is_base64_in_dict(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {}, b"\xff\xfe")
        data = event.to_dict()
        assert data["body_encoding"] == "base64"
        assert data["body"] == "//4="

    def test_utf8_body_is_plain_in_dict(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {}, "héllo".encode())
        data = event.to_dict()
        assert data["body_encoding"] == "utf-8"
        assert data["body"] == "héllo"

    def test_summary_omits_full_body(self, store):
        event = store.add_event("alpha", "POST", "/in/alpha", {}, b"y" * 500)
        summary = event.to_dict(include_body=False)
        assert "body" not in summary
        assert len(summary["body_preview"]) == 200


class TestListEvents:
    def test_newest_first(self, store):
        for index in range(3):
            store.add_event("alpha", "POST", "/in/alpha", {}, str(index).encode())
        events = store.list_events()
        assert [e.body for e in events] == [b"2", b"1", b"0"]

    def test_filters_by_bin(self, store):
        store.add_event("alpha", "POST", "/in/alpha")
        store.add_event("beta", "POST", "/in/beta")
        assert [e.bin for e in store.list_events(bin="beta")] == ["beta"]

    def test_before_id_pages_backwards(self, store):
        ids = [
            store.add_event("alpha", "POST", "/in/alpha", {}, str(i).encode()).id
            for i in range(4)
        ]
        page = store.list_events(limit=10, before_id=ids[2])
        assert [e.id for e in page] == [ids[1], ids[0]]

    def test_limit_is_clamped(self, store):
        for _ in range(5):
            store.add_event("alpha", "POST", "/in/alpha")
        assert len(store.list_events(limit=0)) == 1
        assert len(store.list_events(limit=2)) == 2
        assert len(store.list_events(limit=10_000)) == 5

    def test_invalid_bin_filter_raises(self, store):
        with pytest.raises(ValidationError):
            store.list_events(bin="not valid")


class TestSingleEvents:
    def test_get_and_delete(self, store):
        event = store.add_event("alpha", "PUT", "/in/alpha/1", {}, b"body")
        fetched = store.get_event(event.id)
        assert fetched is not None
        assert fetched.method == "PUT"
        assert fetched.path == "/in/alpha/1"

        assert store.delete_event(event.id) is True
        assert store.get_event(event.id) is None
        assert store.delete_event(event.id) is False

    def test_get_missing_returns_none(self, store):
        assert store.get_event(999) is None


class TestPersistence:
    def test_survives_reopen(self, tmp_path):
        db_path = str(tmp_path / "inbox.db")
        first = InboxStore(db_path)
        first.add_event("alpha", "POST", "/in/alpha", {"x": "1"}, b"kept")
        first.close()

        second = InboxStore(db_path)
        try:
            events = second.list_events()
            assert len(events) == 1
            assert events[0].body == b"kept"
            assert events[0].headers["x"] == "1"
        finally:
            second.close()
