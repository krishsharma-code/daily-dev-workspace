"""Cursor (keyset) pagination with opaque, tamper-evident cursors.

Offset pagination (``LIMIT 20 OFFSET 400``) re-reads and re-skips every row
before the page you asked for, and it silently drops or duplicates rows when
the underlying data changes between requests. Keyset pagination asks for
"everything after the row I last saw", which stays correct and stays cheap.

This module is the small, dependency-free half of that: it turns the sort-key
values of the last row on a page into an opaque cursor string, and turns that
cursor back into the position to resume from.

    from pagination_kit import paginate

    page = paginate(rows, sort_keys=("created_at", "id"), limit=2)
    page.next_cursor            # 'eyJkIjoiYXNjIiwia1si...' or None
    nxt = paginate(rows, sort_keys=("created_at", "id"), limit=2,
                   cursor=page.next_cursor)

Nothing here talks to a database, so the same rules apply whether the rows come
from SQL, a JSON API, a CSV or a list in memory.

Design notes
------------
* The cursor is *not* a security token and not an authorisation mechanism: it
  carries only the sort-key values of the row you last saw. It is base64url
  encoded JSON with a version byte so a future format change can be detected
  instead of misread.
* A cursor is treated as untrusted input. Malformed, truncated, padded,
  oversized, wrong-version and wrong-direction cursors all raise
  ``CursorError``; none of them ever reach a comparison or a lookup.
* Sort keys must identify a row uniquely. Without a unique tiebreaker two rows
  sharing the last row's key would both be skipped by the next page, so that
  case raises instead of quietly losing data.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "CURSOR_VERSION",
    "DEFAULT_LIMIT",
    "MAX_CURSOR_LENGTH",
    "MAX_LIMIT",
    "Cursor",
    "CursorError",
    "Page",
    "PaginationError",
    "decode_cursor",
    "encode_cursor",
    "iter_pages",
    "paginate",
]

CURSOR_VERSION = 1
"""Format version written into every cursor this build produces."""

DEFAULT_LIMIT = 20
"""Page size used when the caller does not ask for one."""

MAX_LIMIT = 100
"""Largest page this module will serve, however large a limit is requested."""

MAX_CURSOR_LENGTH = 4096
"""Longest cursor accepted, in characters. Anything longer is rejected unread."""

MAX_SORT_KEYS = 8
MAX_VALUE_LENGTH = 512
DIRECTIONS = ("asc", "desc")

_CURSOR_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


class PaginationError(ValueError):
    """Base error for bad page requests and bad data."""


class CursorError(PaginationError):
    """The cursor string is malformed, unsupported, or does not fit the request."""


# ---------------------------------------------------------------------------
# cursor primitives
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Cursor:
    """A decoded position: the sort-key values of the last row already served."""

    values: tuple[Any, ...]
    direction: str

    def __str__(self) -> str:  # pragma: no cover - convenience only
        return encode_cursor(self.values, direction=self.direction)


def _check_direction(direction: Any) -> str:
    if not isinstance(direction, str):
        raise PaginationError(
            f"direction must be 'asc' or 'desc', got {type(direction).__name__}"
        )
    lowered = direction.strip().lower()
    if lowered not in DIRECTIONS:
        raise PaginationError(
            f"direction must be 'asc' or 'desc', got {direction!r}"
        )
    return lowered


def _check_value(value: Any, where: str, error: type[PaginationError]) -> Any:
    """Accept only the scalars a cursor can carry through JSON unchanged."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise error(f"{where} must be a finite number, got {value!r}")
        return value
    if isinstance(value, str):
        if len(value) > MAX_VALUE_LENGTH:
            raise error(
                f"{where} is {len(value)} characters, "
                f"over the {MAX_VALUE_LENGTH}-character limit"
            )
        return value
    raise error(
        f"{where} must be a str, int, float, bool or None, "
        f"got {type(value).__name__}"
    )


def _check_values(
    values: Any, where: str, error: type[PaginationError]
) -> tuple[Any, ...]:
    if isinstance(values, (str, bytes)):
        raise error(f"{where} must be a sequence of key values, not a string")
    if not isinstance(values, (tuple, list)):
        raise error(
            f"{where} must be a tuple or list, got {type(values).__name__}"
        )
    if not values:
        raise error(f"{where} must contain at least one value")
    if len(values) > MAX_SORT_KEYS:
        raise error(
            f"{where} has {len(values)} values, over the {MAX_SORT_KEYS} limit"
        )
    return tuple(
        _check_value(value, f"{where} value {index}", error)
        for index, value in enumerate(values)
    )


def encode_cursor(values: Sequence[Any], *, direction: str = "asc") -> str:
    """Encode the sort-key values of the last served row into an opaque cursor.

    ``values`` must be in the same order as the ``sort_keys`` you paginate by.
    """
    direction = _check_direction(direction)
    checked = _check_values(values, "cursor values", PaginationError)
    payload = {"v": CURSOR_VERSION, "d": direction, "k": list(checked)}
    raw = json.dumps(
        payload, separators=(",", ":"), ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    if len(cursor) > MAX_CURSOR_LENGTH:
        raise PaginationError(
            f"encoded cursor is {len(cursor)} characters, "
            f"over the {MAX_CURSOR_LENGTH}-character limit"
        )
    return cursor


def decode_cursor(cursor: str) -> Cursor:
    """Decode a cursor produced by :func:`encode_cursor`.

    Raises :class:`CursorError` for anything else — the caller cannot tell a
    hand-edited cursor from a corrupt one, and neither should we.
    """
    if not isinstance(cursor, str):
        raise CursorError(
            f"cursor must be a string, got {type(cursor).__name__}"
        )
    text = cursor.strip()
    if not text:
        raise CursorError("cursor is empty")
    if len(text) > MAX_CURSOR_LENGTH:
        raise CursorError(
            f"cursor is {len(text)} characters, "
            f"over the {MAX_CURSOR_LENGTH}-character limit"
        )
    text = text.rstrip("=")
    if not text:
        raise CursorError("cursor is empty")
    bad = sorted({char for char in text if char not in _CURSOR_ALPHABET})
    if bad:
        shown = ", ".join(repr(char) for char in bad[:3])
        raise CursorError(
            f"cursor is not base64url: unexpected character(s) {shown}"
        )

    padded = text + "=" * (-len(text) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (binascii.Error, ValueError) as exc:
        raise CursorError(f"cursor is not valid base64url: {exc}") from None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise CursorError("cursor does not decode to UTF-8 text") from None
    except json.JSONDecodeError as exc:
        raise CursorError(f"cursor does not contain JSON: {exc}") from None

    if not isinstance(payload, dict):
        raise CursorError(
            f"cursor payload must be an object, got {type(payload).__name__}"
        )
    version = payload.get("v")
    if not isinstance(version, int) or isinstance(version, bool):
        raise CursorError("cursor is missing its version field")
    if version != CURSOR_VERSION:
        raise CursorError(
            f"unsupported cursor version {version}; "
            f"this build understands version {CURSOR_VERSION}"
        )
    direction = payload.get("d")
    if direction not in DIRECTIONS:
        raise CursorError(f"cursor has an invalid direction {direction!r}")
    if "k" not in payload:
        raise CursorError("cursor is missing its key values")
    values = _check_values(payload["k"], "cursor", CursorError)
    return Cursor(values=values, direction=direction)


# ---------------------------------------------------------------------------
# page requests
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """One page of results plus the cursor needed to ask for the next one."""

    items: tuple[Any, ...]
    next_cursor: str | None
    has_more: bool
    limit: int
    sort_keys: tuple[str, ...]
    direction: str

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    @property
    def count(self) -> int:
        """Number of items on this page (``len(page)`` reads the same)."""
        return len(self.items)

    @property
    def is_empty(self) -> bool:
        return not self.items


def _check_sort_keys(sort_keys: Any) -> tuple[str, ...]:
    if isinstance(sort_keys, str):
        sort_keys = (sort_keys,)
    if not isinstance(sort_keys, (tuple, list)):
        raise PaginationError(
            "sort_keys must be a string or a sequence of strings, "
            f"got {type(sort_keys).__name__}"
        )
    if not sort_keys:
        raise PaginationError("sort_keys must name at least one key")
    if len(sort_keys) > MAX_SORT_KEYS:
        raise PaginationError(
            f"sort_keys has {len(sort_keys)} entries, over the {MAX_SORT_KEYS} limit"
        )
    checked: list[str] = []
    for name in sort_keys:
        if not isinstance(name, str):
            raise PaginationError(
                f"sort key names must be strings, got {type(name).__name__}"
            )
        name = name.strip()
        if not name:
            raise PaginationError("sort key names must not be blank")
        if name in checked:
            raise PaginationError(f"duplicate sort key {name!r}")
        checked.append(name)
    return tuple(checked)


def _check_limit(limit: Any, max_limit: Any) -> tuple[int, int]:
    for name, value in (("limit", limit), ("max_limit", max_limit)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise PaginationError(
                f"{name} must be an integer, got {type(value).__name__}"
            )
        if value < 1:
            raise PaginationError(f"{name} must be at least 1, got {value}")
    if limit > max_limit:
        limit = max_limit
    return limit, max_limit


def _lookup(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        if name not in item:
            raise PaginationError(f"item has no sort key {name!r}")
        return item[name]
    try:
        return getattr(item, name)
    except AttributeError:
        raise PaginationError(
            f"item has no sort key {name!r}: {type(item).__name__} has no "
            f"attribute {name!r} and is not a mapping"
        ) from None


def paginate(
    items: Iterable[Any],
    *,
    sort_keys: str | Sequence[str],
    limit: int = DEFAULT_LIMIT,
    cursor: str | Cursor | None = None,
    direction: str = "asc",
    max_limit: int = MAX_LIMIT,
) -> Page:
    """Return one page of ``items`` ordered by ``sort_keys``.

    ``items`` may be a mapping of records, or any object with attributes named
    by ``sort_keys``. ``cursor`` is either a cursor string from a previous
    :class:`Page` or an already-decoded :class:`Cursor`.
    """
    keys = _check_sort_keys(sort_keys)
    direction = _check_direction(direction)
    limit, max_limit = _check_limit(limit, max_limit)

    if cursor is None:
        after: tuple[Any, ...] | None = None
    else:
        decoded = decode_cursor(cursor) if isinstance(cursor, str) else cursor
        if not isinstance(decoded, Cursor):
            raise CursorError(
                "cursor must be a string or a Cursor, "
                f"got {type(decoded).__name__}"
            )
        if decoded.direction != direction:
            raise CursorError(
                f"cursor was issued for {decoded.direction} order but this "
                f"request asks for {direction} order"
            )
        if len(decoded.values) != len(keys):
            raise CursorError(
                f"cursor carries {len(decoded.values)} key value(s) but this "
                f"request sorts by {len(keys)} key(s)"
            )
        after = decoded.values

    rows: list[tuple[tuple[Any, ...], Any]] = []
    for item in items:
        values = tuple(
            _check_value(
                _lookup(item, name), f"sort key {name!r}", PaginationError
            )
            for name in keys
        )
        rows.append((values, item))

    try:
        rows.sort(key=lambda row: row[0], reverse=direction == "desc")
    except TypeError as exc:
        raise PaginationError(
            f"sort key values are not comparable with each other: {exc}"
        ) from None

    if len({row[0] for row in rows}) != len(rows):
        raise PaginationError(
            "sort keys do not identify items uniquely; "
            "add a tiebreaker key such as 'id'"
        )

    if after is not None:
        try:
            if direction == "desc":
                rows = [row for row in rows if row[0] < after]
            else:
                rows = [row for row in rows if row[0] > after]
        except TypeError as exc:
            raise CursorError(
                f"cursor values are not comparable with this data: {exc}"
            ) from None

    has_more = len(rows) > limit
    window = rows[:limit]
    next_cursor = (
        encode_cursor(window[-1][0], direction=direction) if has_more else None
    )
    return Page(
        items=tuple(item for _, item in window),
        next_cursor=next_cursor,
        has_more=has_more,
        limit=limit,
        sort_keys=keys,
        direction=direction,
    )


def iter_pages(
    items: Iterable[Any],
    *,
    sort_keys: str | Sequence[str],
    limit: int = DEFAULT_LIMIT,
    direction: str = "asc",
    max_limit: int = MAX_LIMIT,
) -> Iterator[Page]:
    """Yield every page in order, following each page's own ``next_cursor``.

    ``items`` is materialised once, so the pages are consistent with each other.
    """
    rows = list(items)
    cursor: str | None = None
    seen: set[str] = set()
    while True:
        page = paginate(
            rows,
            sort_keys=sort_keys,
            limit=limit,
            cursor=cursor,
            direction=direction,
            max_limit=max_limit,
        )
        yield page
        if not page.has_more:
            return
        if page.next_cursor in seen:
            raise PaginationError(
                "pagination is not advancing; the cursor repeated itself"
            )
        seen.add(page.next_cursor)
        cursor = page.next_cursor
