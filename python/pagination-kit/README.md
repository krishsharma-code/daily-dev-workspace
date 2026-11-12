# pagination-kit

Keyset ("cursor") pagination for lists of records, with opaque cursors and
strict validation of everything that comes back from the client. Standard
library only — no ORM, no database driver, no dependency.

Offset pagination (`LIMIT 20 OFFSET 400`) re-reads every row before the page
you asked for, and it drops or duplicates rows when data changes between
requests. Keyset pagination asks for "everything after the row I last saw",
which stays correct and stays cheap. This module is the portable half of that:
the cursor format, the comparison, the clamping and the error handling — so the
same rules apply whether the rows come from SQL, a JSON API, a CSV or a plain
list.

```python
from pagination_kit import paginate

page = paginate(orders, sort_keys=("created_at", "id"), limit=20)
page.items                      # tuple of the first 20 orders
page.has_more                   # True
page.next_cursor                # 'eyJkIjoiYXNjIiwiayI6...'  (or None)

nxt = paginate(orders, sort_keys=("created_at", "id"), limit=20,
               cursor=page.next_cursor)
```

`items` can be mappings (dicts) or objects with attributes, so the same call
works for `sqlite3.Row`-style dicts and for dataclasses.

## Why a cursor instead of an offset

- **No skipped or repeated rows.** The cursor is a position in the sort order,
  not a count of rows to jump over. Inserting or deleting a row elsewhere in
  the result set does not shift the page you are reading.
- **The cost is per page, not per page number.** Page 400 costs the same as
  page 1.
- **The client cannot ask for arbitrary positions.** A cursor is produced by
  the server and validated on the way back in, so a hand-edited value is a
  clear `CursorError` rather than a wrong page.

## API

| Name | Purpose |
| --- | --- |
| `paginate(items, *, sort_keys, limit=20, cursor=None, direction="asc", max_limit=100)` | One page of results, ordered by `sort_keys`. |
| `iter_pages(items, *, sort_keys, limit=20, direction="asc", max_limit=100)` | Generator over every page, following each page's own cursor. |
| `encode_cursor(values, *, direction="asc")` | Turn the sort-key values of the last served row into a cursor string. |
| `decode_cursor(cursor)` | Parse a cursor string back into a `Cursor`, or raise `CursorError`. |
| `Page` | Frozen record: `items`, `next_cursor`, `has_more`, `limit`, `sort_keys`, `direction`; plus `count`, `is_empty`, `len(page)` and iteration. |
| `Cursor` | Frozen `values` + `direction` pair; `str(cursor)` re-encodes it. |
| `PaginationError` | `ValueError` subclass for bad requests and unusable data. |
| `CursorError` | `PaginationError` subclass for anything wrong with a cursor. |
| `DEFAULT_LIMIT`, `MAX_LIMIT`, `MAX_CURSOR_LENGTH`, `CURSOR_VERSION` | The constants behind the rules below. |

## Rules the module enforces

**Page size.** `limit` must be a real `int` (booleans and floats are rejected,
because `True` is not a page size) and at least 1. It is clamped to
`max_limit`, default 100, so `?limit=5000` returns a 100-row page instead of
dumping the table.

**Sort keys.** At least one key, at most eight, no blanks, no duplicates. Each
key must exist on every item, as a mapping key or an attribute. The values must
be mutually comparable — mixing `int` and `str` in one key raises rather than
silently sorting by type name.

**Unique tiebreaker.** The combination of sort-key values must identify an item
uniquely. Two rows sharing the last row's key would both be skipped by the next
page, so `paginate(rows, sort_keys="created_at")` with a duplicate timestamp
raises `PaginationError` telling you to add `id`. This is the one rule people
trip over, and it is the one that silently loses data if it is not enforced.

**Cursor contents.** Only `str`, `int`, `float`, `bool` and `None` survive a
round trip through JSON, so only those are accepted as sort-key values.
Non-finite floats, strings over 512 characters and cursors over 4096 characters
are rejected. A cursor is base64url-encoded JSON, unpadded, with a version field
so a future format change is detected instead of misread:

```json
{"v": 1, "d": "asc", "k": ["2026-11-12T09:15:00Z", 102]}
```

**Direction.** `direction` accepts `"asc"` or `"desc"` in any case. A cursor
carries the direction it was issued for; replaying it against the other
direction, or against a different number of sort keys, raises `CursorError`.
Both would otherwise return a plausible-looking but wrong page.

**Untrusted input.** `decode_cursor` rejects non-strings, empty strings,
oversized strings, characters outside the base64url alphabet, invalid base64,
non-JSON bytes, non-object payloads, a missing or non-integer version, unknown
versions, invalid directions, missing or empty key lists, and values of the
wrong type. Every one of those is a `CursorError`; nothing reaches a comparison
or a lookup.

## Design notes

- **The cursor is a position, not a token.** It carries no signature and no
  authorisation. If a cursor must also be unforgeable — for example to stop a
  tenant enumerating another tenant's rows — sign it at the edge and verify
  before calling `paginate`. The values are already a plain, inspectable JSON
  array, so HMAC-ing the cursor string is enough.
- **`next_cursor` is only set when there is a next page.** `has_more=False`
  always comes with `next_cursor=None`, so a client cannot loop forever on the
  last page.
- **`paginate` never mutates the input.** It sorts a private list of
  `(key values, item)` pairs, so passing a list you still need is safe.
- **`iter_pages` materialises the input once**, so all its pages are consistent
  with each other. It also raises if a cursor ever repeats, which turns a bug in
  a future change into an error instead of an infinite loop.
- **Errors are specific.** Messages name the offending key, limit or cursor
  field, so they can go straight into a 400 response.

## Wiring it to a real store

`paginate` is deliberately in-memory. For a database, keep the same shape and
push the comparison into the query — the cursor format does not change:

```python
cursor = decode_cursor(request.args.get("cursor"))
rows = db.execute(
    "SELECT * FROM orders "
    "WHERE (created_at, id) > (?, ?) "
    "ORDER BY created_at, id LIMIT ?",
    (*cursor.values, 21),          # fetch one extra row to detect has_more
).fetchall()
```

## Run the demo

```bash
cd python/pagination-kit
python example.py
```

It walks a seven-order list page by page, shows the decoded cursor, clamps a
`limit=5000` request, replays a hand-edited cursor and an unknown-version
cursor, and shows the duplicate-timestamp error that the unique-tiebreaker rule
prevents.

## Run the tests

```bash
cd python/pagination-kit
python -m pytest
```

108 tests: cursor round trips for every supported scalar including unicode and
large integers, direction normalisation, padding and whitespace tolerance, all
malformed-cursor paths (charset, base64, JSON, version, direction, key list,
value type, oversize), limit and `max_limit` validation with booleans and
floats rejected, sort-key validation, missing mapping keys and missing
attributes, incomparable and non-scalar key values, the unique-tiebreaker rule,
ascending and descending walks that must visit every item exactly once, exact
page boundaries, cursors past the end, cursor/data arity and direction
mismatches, `Page` behaviour, `iter_pages` including the empty case and the
repeated-cursor guard, and an AST check that the module imports nothing outside
the standard library. `pytest` is needed only to run the suite.
