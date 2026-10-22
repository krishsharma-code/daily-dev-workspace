"""Tests for the Link Vault storage layer (``server/store.py``)."""

import sqlite3

import pytest

from store import Link, LinkStore, ValidationError, normalize_tags, normalize_url


class TestNormalizeUrl:
    def test_keeps_explicit_https(self):
        assert normalize_url("https://example.com/x") == "https://example.com/x"

    def test_keeps_explicit_http(self):
        assert normalize_url("http://example.com") == "http://example.com"

    def test_adds_scheme_to_bare_host(self):
        assert normalize_url("example.com") == "https://example.com"

    def test_trims_whitespace(self):
        assert normalize_url("  https://example.com  ") == "https://example.com"

    @pytest.mark.parametrize("bad", ["", "   ", "ftp://example.com", "https://"])
    def test_rejects_bad_urls(self, bad):
        with pytest.raises(ValidationError):
            normalize_url(bad)

    def test_rejects_non_string(self):
        with pytest.raises(ValidationError):
            normalize_url(123)


class TestNormalizeTags:
    def test_lowercases_strips_and_dedupes_keeping_order(self):
        assert normalize_tags([" Web ", "PYTHON", "web", ""]) == ("web", "python")

    def test_accepts_comma_string(self):
        assert normalize_tags("a, b ,a") == ("a", "b")

    def test_none_and_empty(self):
        assert normalize_tags(None) == ()
        assert normalize_tags([]) == ()

    def test_rejects_non_string_tag(self):
        with pytest.raises(ValidationError):
            normalize_tags([1])


class TestLinkStore:
    def setup_method(self):
        self.store = LinkStore(":memory:")

    def teardown_method(self):
        self.store.close()

    def test_add_returns_link_with_defaults(self):
        link = self.store.add("https://example.com", title="Example", tags=["Docs"])
        assert isinstance(link, Link)
        assert link.id == 1
        assert link.url == "https://example.com"
        assert link.title == "Example"
        assert link.tags == ("docs",)
        assert link.created_at.endswith("+00:00")

    def test_title_defaults_to_host(self):
        link = self.store.add("https://news.ycombinator.com")
        assert link.title == "news.ycombinator.com"

    def test_add_validates_url(self):
        with pytest.raises(ValidationError):
            self.store.add("ftp://nope")

    def test_get_roundtrip_and_missing(self):
        link = self.store.add("https://a.example")
        assert self.store.get(link.id).to_dict() == link.to_dict()
        assert self.store.get(999) is None

    def test_list_is_newest_first(self):
        first = self.store.add("https://one.example")
        second = self.store.add("https://two.example")
        assert [link.id for link in self.store.list()] == [second.id, first.id]

    def test_list_filters_by_tag(self):
        self.store.add("https://a.example", tags=["python", "web"])
        self.store.add("https://b.example", tags=["web"])
        results = self.store.list(tag="python")
        assert [link.url for link in results] == ["https://a.example"]

    def test_tag_filter_is_not_substring(self):
        self.store.add("https://a.example", tags=["webdev"])
        assert self.store.list(tag="web") == []

    def test_list_filters_by_query(self):
        self.store.add("https://a.example", title="Python docs")
        self.store.add("https://b.example", title="Other")
        assert [link.url for link in self.store.list(query="python")] == ["https://a.example"]

    def test_list_combines_tag_and_query(self):
        self.store.add("https://a.example", title="Python docs", tags=["python"])
        self.store.add("https://b.example", title="Python blog", tags=["web"])
        results = self.store.list(tag="python", query="docs")
        assert [link.url for link in results] == ["https://a.example"]

    def test_delete(self):
        link = self.store.add("https://a.example")
        assert self.store.delete(link.id) is True
        assert self.store.delete(link.id) is False
        assert self.store.count() == 0

    def test_count(self):
        assert self.store.count() == 0
        self.store.add("https://a.example")
        self.store.add("https://b.example")
        assert self.store.count() == 2

    def test_persists_to_file(self, tmp_path):
        db = str(tmp_path / "links.db")
        first = LinkStore(db)
        first.add("https://persist.example", title="Saved", tags=["keep"])
        first.close()

        second = LinkStore(db)
        try:
            links = second.list()
            assert len(links) == 1
            assert links[0].title == "Saved"
            assert links[0].tags == ("keep",)
        finally:
            second.close()

    def test_link_to_dict_shape(self):
        link = self.store.add("https://a.example", tags=["x"])
        payload = link.to_dict()
        assert set(payload) == {"id", "url", "title", "tags", "created_at"}
        assert payload["tags"] == ["x"]

    def test_schema_created_once(self):
        # Re-instantiating against the same in-memory connection path is not
        # meaningful, so assert the table exists and is queryable.
        row = self.store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='links'"
        ).fetchone()
        assert row is not None
        assert isinstance(self.store._conn, sqlite3.Connection)
