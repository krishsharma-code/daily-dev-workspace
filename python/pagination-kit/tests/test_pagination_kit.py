"""Tests for pagination_kit."""

import ast
import os

import pytest

import pagination_kit
from pagination_kit import (
    CURSOR_VERSION,
    DEFAULT_LIMIT,
    MAX_CURSOR_LENGTH,
    MAX_LIMIT,
    Cursor,
    CursorError,
    Page,
    PaginationError,
    decode_cursor,
    encode_cursor,
    iter_pages,
    paginate,
)


class Row:
    """A record that exposes its fields as attributes rather than keys."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"Row({self.__dict__})"


def rows(count=10, start=0):
    return [{"id": index} for index in range(start, start + count)]


def ids(page):
    return [item["id"] for item in page.items]


# ---------------------------------------------------------------------------
# cursor encoding and decoding
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "values",
    [
        ("alpha",),
        (42,),
        (-7,),
        (3.5,),
        (True,),
        (False,),
        (None,),
        ("2026-11-12T15:38:11Z", 7),
        ("", 0, None),
        (1, 2, 3, 4, 5, 6, 7, 8),
        ("Ünïcødé ✓ 東京", 12),
        (10**20, -10**20),
    ],
)
def test_cursor_round_trips_every_supported_scalar(values):
    decoded = decode_cursor(encode_cursor(values))
    assert decoded.values == values
    assert decoded.direction == "asc"


@pytest.mark.parametrize("direction", ["asc", "desc", "ASC", " Desc "])
def test_cursor_round_trips_direction_and_normalises_it(direction):
    decoded = decode_cursor(encode_cursor((1,), direction=direction))
    assert decoded.direction == direction.strip().lower()


def test_cursor_is_unpadded_base64url():
    cursor = encode_cursor(("a" * 40,))
    assert "=" not in cursor
    assert "+" not in cursor and "/" not in cursor
    assert all(char.isalnum() or char in "-_" for char in cursor)


def test_cursor_accepts_lists_as_well_as_tuples():
    assert decode_cursor(encode_cursor(["x", 1])).values == ("x", 1)


def test_decode_tolerates_surrounding_whitespace_and_padding():
    cursor = encode_cursor(("keep", 1))
    assert decode_cursor(f"  {cursor}  ").values == ("keep", 1)
    assert decode_cursor(cursor + "=" * (-len(cursor) % 4)).values == ("keep", 1)


def test_cursor_str_round_trips_through_decode():
    original = Cursor(values=("a", 2), direction="desc")
    assert decode_cursor(str(original)) == original


def test_cursor_is_hashable_and_comparable():
    one = Cursor(values=("a",), direction="asc")
    two = Cursor(values=("a",), direction="asc")
    assert one == two
    assert len({one, two}) == 1


def test_cursor_error_is_a_value_error():
    assert issubclass(CursorError, PaginationError)
    assert issubclass(PaginationError, ValueError)


@pytest.mark.parametrize(
    "values, message",
    [
        ((), "at least one value"),
        ("not-a-list", "not a string"),
        (b"bytes", "not a string"),
        ({"a": 1}, "tuple or list"),
        ((object(),), "must be a str, int, float, bool or None"),
        (([1, 2],), "must be a str, int, float, bool or None"),
        ((float("nan"),), "finite number"),
        ((float("inf"),), "finite number"),
        ((float("-inf"),), "finite number"),
        (("x" * 513,), "over the 512-character limit"),
        ((1, 2, 3, 4, 5, 6, 7, 8, 9), "over the 8 limit"),
    ],
)
def test_encode_rejects_unusable_values(values, message):
    with pytest.raises(PaginationError, match=message):
        encode_cursor(values)


@pytest.mark.parametrize("direction", ["sideways", "", None, 1])
def test_encode_rejects_bad_direction(direction):
    with pytest.raises(PaginationError, match="direction must be"):
        encode_cursor((1,), direction=direction)


def test_encode_rejects_cursor_over_the_length_limit():
    values = tuple("é" * 512 for _ in range(8))
    with pytest.raises(PaginationError, match="encoded cursor is"):
        encode_cursor(values)


@pytest.mark.parametrize(
    "cursor, message",
    [
        (123, "must be a string"),
        (None, "must be a string"),
        ("", "cursor is empty"),
        ("   ", "cursor is empty"),
        ("=", "cursor is empty"),
        ("abc$def", "not base64url"),
        ("a", "not valid base64url"),
        ("e30", "missing its version field"),  # {}
        ("WzEsMl0", "payload must be an object"),  # [1,2]
        ("bm90IGpzb24", "does not contain JSON"),  # "not json"
        # {"v": true, "d": "asc", "k": [1]} — a bool is not a version number
        ("eyJ2Ijp0cnVlLCJkIjoiYXNjIiwiayI6WzFdfQ", "missing its version field"),
        # {"v": 99, "d": "asc", "k": ["a"]}
        ("eyJ2Ijo5OSwiZCI6ImFzYyIsImsiOlsiYSJdfQ", "unsupported cursor version 99"),
        # {"v": 1, "d": "up", "k": ["a"]}
        ("eyJ2IjoxLCJkIjoidXAiLCJrIjpbImEiXX0", "invalid direction"),
        # {"v": 1, "d": "asc"}
        ("eyJ2IjoxLCJkIjoiYXNjIn0", "missing its key values"),
        # {"v": 1, "d": "asc", "k": []}
        ("eyJ2IjoxLCJkIjoiYXNjIiwiayI6W119", "at least one value"),
        # {"v": 1, "d": "asc", "k": [{}]}
        ("eyJ2IjoxLCJkIjoiYXNjIiwiayI6W3t9XX0", "must be a str, int, float"),
    ],
)
def test_decode_rejects_malformed_cursors(cursor, message):
    with pytest.raises(CursorError, match=message):
        decode_cursor(cursor)


def test_decode_rejects_oversized_cursor_without_decoding_it():
    with pytest.raises(CursorError, match="over the 4096-character limit"):
        decode_cursor("A" * (MAX_CURSOR_LENGTH + 1))


# ---------------------------------------------------------------------------
# paginate: validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "limit, message",
    [(0, "at least 1"), (-3, "at least 1"), (True, "must be an integer"),
     (2.5, "must be an integer"), ("5", "must be an integer")],
)
def test_paginate_rejects_bad_limit(limit, message):
    with pytest.raises(PaginationError, match=message):
        paginate(rows(), sort_keys="id", limit=limit)


def test_paginate_rejects_bad_max_limit():
    with pytest.raises(PaginationError, match="max_limit must be at least 1"):
        paginate(rows(), sort_keys="id", max_limit=0)


@pytest.mark.parametrize(
    "sort_keys, message",
    [
        ((), "at least one key"),
        ([], "at least one key"),
        (5, "must be a string or a sequence of strings"),
        (("",), "must not be blank"),
        (("id", "id"), "duplicate sort key"),
        ((1,), "sort key names must be strings"),
        (("a", "b", "c", "d", "e", "f", "g", "h", "i"), "over the 8 limit"),
    ],
)
def test_paginate_rejects_bad_sort_keys(sort_keys, message):
    with pytest.raises(PaginationError, match=message):
        paginate(rows(), sort_keys=sort_keys)


@pytest.mark.parametrize("direction", ["up", "", None])
def test_paginate_rejects_bad_direction(direction):
    with pytest.raises(PaginationError, match="direction must be"):
        paginate(rows(), sort_keys="id", direction=direction)


def test_paginate_rejects_missing_mapping_key():
    with pytest.raises(PaginationError, match="has no sort key 'name'"):
        paginate([{"id": 1}], sort_keys=("id", "name"))


def test_paginate_rejects_missing_attribute_on_objects():
    with pytest.raises(PaginationError, match="has no attribute 'name'"):
        paginate([Row(id=1)], sort_keys="name")


def test_paginate_rejects_incomparable_sort_values():
    data = [{"k": 1}, {"k": "one"}]
    with pytest.raises(PaginationError, match="not comparable"):
        paginate(data, sort_keys="k")


def test_paginate_rejects_non_scalar_sort_values():
    with pytest.raises(PaginationError, match="sort key 'k'"):
        paginate([{"k": [1, 2]}], sort_keys="k")


def test_paginate_requires_a_unique_tiebreaker():
    data = [{"id": 1, "at": "2026-11-12"}, {"id": 2, "at": "2026-11-12"}]
    with pytest.raises(PaginationError, match="add a tiebreaker"):
        paginate(data, sort_keys="at")
    assert ids(paginate(data, sort_keys=("at", "id"))) == [1, 2]


# ---------------------------------------------------------------------------
# paginate: paging behaviour
# ---------------------------------------------------------------------------


def test_first_page_is_ordered_and_reports_more():
    page = paginate(rows(10), sort_keys="id", limit=3)
    assert ids(page) == [0, 1, 2]
    assert page.has_more is True
    assert page.next_cursor is not None
    assert page.limit == 3
    assert page.sort_keys == ("id",)
    assert page.direction == "asc"


def test_last_page_has_no_cursor():
    page = paginate(rows(3), sort_keys="id", limit=5)
    assert ids(page) == [0, 1, 2]
    assert page.has_more is False
    assert page.next_cursor is None


def test_page_exactly_filled_by_limit_has_no_cursor():
    page = paginate(rows(6), sort_keys="id", limit=3, cursor=encode_cursor((2,)))
    assert ids(page) == [3, 4, 5]
    assert page.has_more is False
    assert page.next_cursor is None


def test_default_limit_and_clamping():
    assert paginate(rows(50), sort_keys="id").limit == DEFAULT_LIMIT
    clamped = paginate(rows(200), sort_keys="id", limit=1000)
    assert clamped.limit == MAX_LIMIT
    assert len(clamped) == MAX_LIMIT
    custom = paginate(rows(200), sort_keys="id", limit=50, max_limit=5)
    assert custom.limit == 5
    assert len(custom) == 5


def test_empty_input_yields_an_empty_page():
    page = paginate([], sort_keys="id")
    assert page.is_empty is True
    assert page.count == 0
    assert bool(page) is False
    assert list(page) == []
    assert page.next_cursor is None
    assert page.has_more is False


def test_cursor_past_the_end_yields_an_empty_final_page():
    page = paginate(rows(5), sort_keys="id", cursor=encode_cursor((4,)))
    assert page.is_empty
    assert page.has_more is False
    assert page.next_cursor is None


def test_walking_pages_visits_every_item_exactly_once():
    data = rows(10)
    seen, cursor, pages = [], None, 0
    while True:
        page = paginate(data, sort_keys="id", limit=3, cursor=cursor)
        seen.extend(ids(page))
        pages += 1
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert seen == list(range(10))
    assert pages == 4


def test_descending_walk_visits_every_item_in_reverse():
    data = rows(7)
    seen, cursor = [], None
    while True:
        page = paginate(data, sort_keys="id", limit=2, direction="desc", cursor=cursor)
        seen.extend(ids(page))
        if not page.has_more:
            break
        cursor = page.next_cursor
    assert seen == list(range(6, -1, -1))


def test_descending_order_starts_from_the_largest_key():
    page = paginate(rows(10), sort_keys="id", limit=3, direction="desc")
    assert ids(page) == [9, 8, 7]


def test_multi_key_sort_orders_by_each_key_in_turn():
    data = [
        {"name": "ravi", "team": "b", "id": 1},
        {"name": "asha", "team": "b", "id": 2},
        {"name": "zoya", "team": "a", "id": 3},
    ]
    page = paginate(data, sort_keys=("team", "name", "id"), limit=10)
    assert [item["name"] for item in page] == ["zoya", "asha", "ravi"]


def test_cursor_carries_every_sort_key_value():
    data = [{"team": "a", "id": index} for index in range(4)]
    page = paginate(data, sort_keys=("team", "id"), limit=2)
    assert decode_cursor(page.next_cursor).values == ("a", 1)
    nxt = paginate(data, sort_keys=("team", "id"), limit=2, cursor=page.next_cursor)
    assert [item["id"] for item in nxt] == [2, 3]


def test_paginate_works_on_objects_with_attributes():
    data = [Row(id=index, at=f"2026-11-{index + 1:02d}") for index in range(3)]
    page = paginate(data, sort_keys=("at", "id"), limit=2)
    assert [item.id for item in page] == [0, 1]
    assert decode_cursor(page.next_cursor).values == ("2026-11-02", 1)


def test_paginate_accepts_a_decoded_cursor_object():
    page = paginate(rows(5), sort_keys="id", limit=2, cursor=Cursor((1,), "asc"))
    assert ids(page) == [2, 3]


def test_paginate_does_not_reorder_the_caller_list():
    data = rows(5)
    original = list(data)
    paginate(data, sort_keys="id", limit=2, direction="desc")
    assert data == original


def test_paginate_rejects_a_cursor_from_the_other_direction():
    cursor = encode_cursor((2,), direction="desc")
    with pytest.raises(CursorError, match="issued for desc order"):
        paginate(rows(5), sort_keys="id", cursor=cursor)


def test_paginate_rejects_a_cursor_with_the_wrong_arity():
    cursor = encode_cursor((1, 2))
    with pytest.raises(CursorError, match="carries 2 key value"):
        paginate(rows(5), sort_keys="id", cursor=cursor)


def test_paginate_rejects_a_non_cursor_argument():
    with pytest.raises(CursorError, match="must be a string"):
        paginate(rows(5), sort_keys="id", cursor=17)


def test_paginate_rejects_a_malformed_cursor_string():
    with pytest.raises(CursorError, match="not base64url"):
        paginate(rows(5), sort_keys="id", cursor="not a cursor!")


def test_paginate_rejects_a_cursor_incompatible_with_the_data():
    data = [{"k": 1}, {"k": 2}]
    cursor = encode_cursor(("two",))
    with pytest.raises(CursorError, match="not comparable with this data"):
        paginate(data, sort_keys="k", cursor=cursor)


def test_paginate_orders_none_before_strings_only_when_comparable():
    data = [{"k": None}, {"k": "a"}]
    with pytest.raises(PaginationError, match="not comparable"):
        paginate(data, sort_keys="k")


def test_float_and_large_integer_keys_survive_the_cursor():
    data = [{"score": value, "id": index} for index, value in enumerate([1.5, 2.25, 10**18])]
    page = paginate(data, sort_keys=("score", "id"), limit=2)
    assert [item["id"] for item in page] == [0, 1]
    nxt = paginate(data, sort_keys=("score", "id"), limit=2, cursor=page.next_cursor)
    assert [item["id"] for item in nxt] == [2]


def test_unicode_keys_survive_the_cursor():
    data = [{"name": "अ"}, {"name": "ब"}, {"name": "स"}]
    page = paginate(data, sort_keys="name", limit=1)
    nxt = paginate(data, sort_keys="name", limit=1, cursor=page.next_cursor)
    assert nxt.items[0]["name"] == "ब"


def test_page_is_frozen():
    page = paginate(rows(2), sort_keys="id")
    with pytest.raises(Exception):
        page.items = ()  # type: ignore[misc]


def test_page_is_a_dataclass_with_the_documented_fields():
    page = paginate(rows(2), sort_keys="id")
    assert isinstance(page, Page)
    assert (page.items, page.limit, page.direction) == (({"id": 0}, {"id": 1}), 20, "asc")


# ---------------------------------------------------------------------------
# iter_pages
# ---------------------------------------------------------------------------


def test_iter_pages_yields_every_item_in_order():
    pages = list(iter_pages(rows(10), sort_keys="id", limit=3))
    assert len(pages) == 4
    assert [item["id"] for page in pages for item in page] == list(range(10))
    assert [page.has_more for page in pages] == [True, True, True, False]


def test_iter_pages_honours_descending_order():
    pages = list(iter_pages(rows(5), sort_keys="id", limit=2, direction="desc"))
    assert [item["id"] for page in pages for item in page] == [4, 3, 2, 1, 0]


def test_iter_pages_yields_one_empty_page_for_no_data():
    pages = list(iter_pages([], sort_keys="id"))
    assert len(pages) == 1
    assert pages[0].is_empty


def test_iter_pages_surfaces_duplicate_key_errors():
    data = [{"id": 1}, {"id": 1}]
    with pytest.raises(PaginationError, match="add a tiebreaker"):
        list(iter_pages(data, sort_keys="id"))


def test_iter_pages_accepts_an_iterator_of_items():
    pages = list(iter_pages(iter(rows(4)), sort_keys="id", limit=2))
    assert sum(page.count for page in pages) == 4


# ---------------------------------------------------------------------------
# module hygiene
# ---------------------------------------------------------------------------


def test_module_imports_only_the_standard_library():
    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, "pagination_kit.py"
    )
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"base64", "binascii", "json", "dataclasses", "typing", "__future__"}


def test_cursor_version_is_an_integer_constant():
    assert isinstance(CURSOR_VERSION, int)
    assert CURSOR_VERSION >= 1


def test_every_exported_name_exists_once():
    exported = pagination_kit.__all__
    assert len(exported) == len(set(exported))
    for name in exported:
        assert hasattr(pagination_kit, name), name
