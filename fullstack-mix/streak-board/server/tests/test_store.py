"""Tests for the SQLite habit store: validation, idempotency, cascade, views."""

import pytest

from store import HabitStore, StoreError, slugify

TODAY = "2026-11-25"  # a Wednesday


@pytest.fixture()
def store():
    with HabitStore(":memory:") as habit_store:
        yield habit_store


def test_slugify_makes_url_safe_ids():
    assert slugify("  Read 20 pages ") == "read-20-pages"
    assert slugify("Gym -- 3x/week") == "gym-3x-week"


def test_slugify_rejects_names_without_letters():
    with pytest.raises(StoreError):
        slugify("!!!")


def test_create_habit_stores_weekdays(store):
    habit = store.create_habit("Read 20 pages", ["mon", "wed", "fri"])
    assert habit["id"] == "read-20-pages"
    assert habit["name"] == "Read 20 pages"
    assert habit["weekdays"] == [0, 2, 4]


def test_create_habit_defaults_to_every_day(store):
    habit = store.create_habit("Drink water")
    assert habit["weekdays"] == [0, 1, 2, 3, 4, 5, 6]


def test_create_habit_normalises_whitespace_in_the_name(store):
    habit = store.create_habit("  Morning   walk  ")
    assert habit["name"] == "Morning walk"


@pytest.mark.parametrize("name", ["", "   ", None, 42])
def test_create_habit_rejects_bad_names(store, name):
    with pytest.raises(StoreError):
        store.create_habit(name)


def test_create_habit_rejects_long_names(store):
    with pytest.raises(StoreError):
        store.create_habit("x" * 81)


def test_create_habit_rejects_bad_weekdays(store):
    with pytest.raises(StoreError):
        store.create_habit("Gym", ["funday"])


def test_duplicate_habit_names_collide_on_the_slug(store):
    store.create_habit("Read 20 pages")
    with pytest.raises(StoreError):
        store.create_habit("read 20 PAGES")


def test_list_habits_is_ordered_by_creation(store):
    store.create_habit("First")
    store.create_habit("Second")
    assert [habit["name"] for habit in store.list_habits()] == ["First", "Second"]


def test_delete_habit_removes_its_checkins(store):
    store.create_habit("Gym")
    store.check_in("gym", "2026-11-24", today=TODAY)
    assert store.delete_habit("gym") is True
    assert store.get_habit("gym") is None
    # Recreating the habit must not resurrect the old history.
    store.create_habit("Gym")
    assert store.checkins("gym") == []


def test_delete_unknown_habit_returns_false(store):
    assert store.delete_habit("nope") is False


def test_check_in_today_by_default(store):
    store.create_habit("Gym")
    result = store.check_in("gym", today=TODAY)
    assert result["created"] is True
    assert result["checkin"]["day"] == TODAY


def test_check_in_is_idempotent(store):
    store.create_habit("Gym")
    first = store.check_in("gym", "2026-11-24", today=TODAY)
    second = store.check_in("gym", "2026-11-24", today=TODAY)
    assert first["created"] is True
    assert second["created"] is False
    assert len(store.checkins("gym")) == 1


def test_check_in_stores_a_note(store):
    store.create_habit("Gym")
    result = store.check_in("gym", "2026-11-24", note="  leg day ", today=TODAY)
    assert result["checkin"]["note"] == "leg day"


def test_check_in_rejects_a_long_note(store):
    store.create_habit("Gym")
    with pytest.raises(StoreError):
        store.check_in("gym", "2026-11-24", note="x" * 281, today=TODAY)


def test_check_in_rejects_future_days(store):
    store.create_habit("Gym")
    with pytest.raises(StoreError):
        store.check_in("gym", "2026-11-26", today=TODAY)


def test_check_in_allows_backfill(store):
    store.create_habit("Gym")
    result = store.check_in("gym", "2026-01-05", today=TODAY)
    assert result["created"] is True


def test_check_in_rejects_bad_days(store):
    store.create_habit("Gym")
    with pytest.raises(StoreError):
        store.check_in("gym", "25/11/2026", today=TODAY)


def test_check_in_unknown_habit_raises(store):
    with pytest.raises(StoreError):
        store.check_in("ghost", today=TODAY)


def test_undo_check_in(store):
    store.create_habit("Gym")
    store.check_in("gym", "2026-11-24", today=TODAY)
    assert store.undo_check_in("gym", "2026-11-24") is True
    assert store.undo_check_in("gym", "2026-11-24") is False
    assert store.checkins("gym") == []


def test_undo_check_in_validates_the_day(store):
    store.create_habit("Gym")
    with pytest.raises(StoreError):
        store.undo_check_in("gym", "not-a-day")


def test_checkins_are_ordered_oldest_first(store):
    store.create_habit("Gym")
    for day in ("2026-11-25", "2026-11-20", "2026-11-23"):
        store.check_in("gym", day, today=TODAY)
    assert store.checkin_days("gym") == ["2026-11-20", "2026-11-23", "2026-11-25"]


def test_habit_view_carries_the_summary(store):
    store.create_habit("Gym", "daily")
    store.check_in("gym", "2026-11-24", today=TODAY)
    view = store.habit_view(store.get_habit("gym"), TODAY)
    assert view["summary"]["current"] == 1
    assert view["summary"]["done_today"] is False
    assert view["summary"]["scheduled_today"] is True


def test_board_counts_due_and_done(store):
    store.create_habit("Gym", "daily")
    store.create_habit("Weigh in", ["sat"])
    store.check_in("gym", TODAY, today=TODAY)
    board = store.board(TODAY)
    assert board["today"] == TODAY
    assert board["counts"] == {"habits": 2, "done_today": 1, "due_today": 1}
    assert len(board["habits"]) == 2


def test_board_of_an_empty_store(store):
    board = store.board(TODAY)
    assert board["habits"] == []
    assert board["counts"] == {"habits": 0, "done_today": 0, "due_today": 0}


def test_data_survives_a_reopen(tmp_path):
    path = str(tmp_path / "habits.db")
    first = HabitStore(path)
    first.create_habit("Gym", ["mon"])
    first.check_in("gym", "2026-11-23", today=TODAY)
    first.close()

    second = HabitStore(path)
    try:
        assert second.checkin_days("gym") == ["2026-11-23"]
        assert second.get_habit("gym")["weekdays"] == [0]
    finally:
        second.close()
