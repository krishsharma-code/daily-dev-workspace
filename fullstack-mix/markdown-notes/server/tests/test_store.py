"""Tests for the notes store: validation, title derivation and CRUD."""

import pytest

from store import DEFAULT_TITLE, MAX_TITLE, NoteStore, ValidationError, derive_title


def test_add_derives_title_from_first_non_empty_line(store):
    note = store.add(body="\n\n# My Heading\n\nbody text")
    assert note.title == "My Heading"
    assert note.body.startswith("\n\n# My Heading")


def test_add_falls_back_to_default_title(store):
    assert store.add(body="   \n\n").title == DEFAULT_TITLE


def test_add_keeps_explicit_title(store):
    note = store.add(title="  Kept  ", body="ignored")
    assert note.title == "Kept"


def test_add_rejects_non_string_body(store):
    with pytest.raises(ValidationError):
        store.add(body=123)


def test_add_rejects_oversized_title(store):
    with pytest.raises(ValidationError):
        store.add(title="x" * (MAX_TITLE + 1))


def test_add_rejects_nul_bytes(store):
    with pytest.raises(ValidationError):
        store.add(body="a\x00b")


def test_list_is_newest_first(store):
    first = store.add(title="one")
    second = store.add(title="two")
    assert [n.id for n in store.list()] == [second.id, first.id]


def test_list_filters_by_query(store):
    store.add(title="Groceries", body="milk")
    store.add(title="Reading", body="chapter on grocery logistics")
    store.add(title="Other", body="nothing here")
    assert {n.title for n in store.list(query="grocer")} == {"Groceries", "Reading"}


def test_list_treats_like_wildcards_literally(store):
    store.add(title="100% done")
    store.add(title="plain")
    assert [n.title for n in store.list(query="%")] == ["100% done"]


def test_list_treats_underscore_literally(store):
    store.add(title="snake_case")
    store.add(title="snakeXcase")
    assert [n.title for n in store.list(query="snake_")] == ["snake_case"]


def test_blank_query_lists_everything(store):
    store.add(title="a")
    store.add(title="b")
    assert len(store.list(query="   ")) == 2


def test_update_changes_fields_and_timestamp(store):
    note = store.add(title="old", body="body")
    updated = store.update(note.id, title="new")
    assert updated is not None
    assert updated.title == "new"
    assert updated.body == "body"
    assert updated.created_at == note.created_at
    assert updated.updated_at >= note.updated_at


def test_update_blank_title_falls_back_to_body(store):
    note = store.add(title="old", body="# Fresh")
    updated = store.update(note.id, title="   ")
    assert updated is not None
    assert updated.title == "Fresh"


def test_update_without_fields_is_rejected(store):
    note = store.add(title="x")
    with pytest.raises(ValidationError):
        store.update(note.id)


def test_update_missing_note_returns_none(store):
    assert store.update(999, title="x") is None


def test_delete_reports_whether_a_row_went(store):
    note = store.add(title="x")
    assert store.delete(note.id) is True
    assert store.delete(note.id) is False
    assert store.count() == 0


def test_count_tracks_rows(store):
    assert store.count() == 0
    store.add(title="a")
    store.add(title="b")
    assert store.count() == 2


def test_data_survives_reopening(tmp_path):
    path = str(tmp_path / "notes.db")
    first = NoteStore(path)
    note = first.add(title="persisted", body="hello")
    first.close()

    second = NoteStore(path)
    try:
        loaded = second.get(note.id)
        assert loaded is not None
        assert loaded.title == "persisted"
        assert loaded.body == "hello"
    finally:
        second.close()


def test_derive_title_strips_heading_markers():
    assert derive_title("### Deep heading") == "Deep heading"
    assert derive_title("") == DEFAULT_TITLE
