"""Tests for the SQLite-backed card store (``server/store.py``)."""

from datetime import datetime, timedelta, timezone

import pytest

from store import DEFAULT_DECK, CardStore, ValidationError

NOW = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


@pytest.fixture()
def store():
    s = CardStore(":memory:")
    yield s
    s.close()


class TestAdd:
    def test_add_returns_card_with_defaults(self, store):
        card = store.add("Q", "A", now=NOW)
        assert card.id == 1
        assert card.front == "Q"
        assert card.back == "A"
        assert card.deck == DEFAULT_DECK
        assert card.ease == 2.5
        assert card.interval_days == 0
        assert card.repetitions == 0
        assert card.lapses == 0
        assert card.due == "2026-01-01T09:00:00+00:00"

    def test_add_trims_fields(self, store):
        card = store.add("  Q  ", "  A  ", now=NOW)
        assert card.front == "Q"
        assert card.back == "A"

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_front_required(self, store, bad):
        with pytest.raises(ValidationError):
            store.add(bad, "A")

    @pytest.mark.parametrize("bad", ["", "   "])
    def test_back_required(self, store, bad):
        with pytest.raises(ValidationError):
            store.add("Q", bad)

    def test_non_string_front_rejected(self, store):
        with pytest.raises(ValidationError):
            store.add(123, "A")

    def test_overlong_field_rejected(self, store):
        with pytest.raises(ValidationError):
            store.add("x" * 501, "A")


class TestDeckNames:
    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_blank_deck_falls_back(self, store, blank):
        assert store.add("Q", "A", deck=blank, now=NOW).deck == DEFAULT_DECK

    def test_deck_whitespace_collapsed(self, store):
        assert store.add("Q", "A", deck="  my   deck ", now=NOW).deck == "my deck"

    def test_overlong_deck_rejected(self, store):
        with pytest.raises(ValidationError):
            store.add("Q", "A", deck="d" * 61)


class TestReads:
    def test_get_missing(self, store):
        assert store.get(99) is None

    def test_list_is_oldest_first(self, store):
        a = store.add("A", "1", now=NOW)
        b = store.add("B", "2", now=NOW)
        assert [c.id for c in store.list()] == [a.id, b.id]

    def test_list_filters_by_deck(self, store):
        store.add("A", "1", deck="math", now=NOW)
        store.add("B", "2", deck="bio", now=NOW)
        cards = store.list(deck="math")
        assert [c.front for c in cards] == ["A"]

    def test_new_cards_are_due_now(self, store):
        store.add("A", "1", now=NOW)
        assert [c.front for c in store.due(now=NOW)] == ["A"]

    def test_reviewed_card_is_not_due_until_interval_passes(self, store):
        card = store.add("A", "1", now=NOW)
        store.review(card.id, 5, now=NOW)  # interval becomes 1 day
        assert store.due(now=NOW) == []
        assert [c.id for c in store.due(now=NOW + DAY)] == [card.id]

    def test_due_filters_by_deck(self, store):
        store.add("A", "1", deck="math", now=NOW)
        store.add("B", "2", deck="bio", now=NOW)
        assert [c.front for c in store.due(deck="bio", now=NOW)] == ["B"]


class TestReview:
    def test_review_updates_schedule(self, store):
        card = store.add("A", "1", now=NOW)
        updated = store.review(card.id, 5, now=NOW)
        assert updated.interval_days == 1
        assert updated.repetitions == 1
        assert updated.ease == pytest.approx(2.6)
        assert updated.due == "2026-01-02T09:00:00+00:00"

    def test_review_missing_returns_none(self, store):
        assert store.review(404, 5, now=NOW) is None

    def test_review_invalid_quality_raises(self, store):
        card = store.add("A", "1", now=NOW)
        with pytest.raises(ValidationError):
            store.review(card.id, 9, now=NOW)

    def test_review_persists_across_reads(self, store):
        card = store.add("A", "1", now=NOW)
        store.review(card.id, 5, now=NOW)
        assert store.get(card.id).repetitions == 1


class TestDeleteAndCounts:
    def test_delete(self, store):
        card = store.add("A", "1", now=NOW)
        assert store.delete(card.id) is True
        assert store.delete(card.id) is False
        assert store.count() == 0

    def test_count_and_due_count(self, store):
        a = store.add("A", "1", now=NOW)
        store.add("B", "2", now=NOW)
        store.review(a.id, 5, now=NOW)
        assert store.count() == 2
        assert store.due_count(now=NOW) == 1
        assert store.due_count(now=NOW + DAY) == 2

    def test_decks_summary(self, store):
        a = store.add("A", "1", deck="math", now=NOW)
        store.add("B", "2", deck="math", now=NOW)
        store.add("C", "3", deck="bio", now=NOW)
        store.review(a.id, 5, now=NOW)
        summary = {d["deck"]: d for d in store.decks(now=NOW)}
        assert summary["math"] == {"deck": "math", "total": 2, "due": 1}
        assert summary["bio"] == {"deck": "bio", "total": 1, "due": 1}


class TestPersistence:
    def test_cards_survive_reopen(self, tmp_path):
        path = str(tmp_path / "cards.db")
        first = CardStore(path)
        card = first.add("Persisted?", "Yes", now=NOW)
        first.review(card.id, 5, now=NOW)
        first.close()

        second = CardStore(path)
        try:
            reloaded = second.get(card.id)
            assert reloaded is not None
            assert reloaded.front == "Persisted?"
            assert reloaded.repetitions == 1
        finally:
            second.close()
