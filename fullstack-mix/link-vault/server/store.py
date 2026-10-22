"""SQLite-backed storage for Link Vault bookmarks.

The store is deliberately dependency-free (standard library only) and safe to
share across threads, which is what the HTTP layer needs: ``http.server``
handles each request on its own thread.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional
from urllib.parse import urlparse

__all__ = ["Link", "LinkStore", "ValidationError", "normalize_tags", "normalize_url"]

_ALLOWED_SCHEMES = ("http", "https")


class ValidationError(ValueError):
    """Raised when a link fails validation (bad URL, blank title, ...)."""


def normalize_url(raw: str) -> str:
    """Return a cleaned URL, or raise :class:`ValidationError`.

    A bare host such as ``example.com`` is upgraded to ``https://example.com``
    so that the common "paste and go" case works. Only ``http``/``https`` URLs
    with a host are accepted.
    """
    if not isinstance(raw, str):
        raise ValidationError("url must be a string")
    candidate = raw.strip()
    if not candidate:
        raise ValidationError("url is required")
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValidationError("url scheme must be http or https")
    if not parsed.netloc:
        raise ValidationError("url must include a host")
    return candidate


def normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
    """Lower-case, strip, de-duplicate and drop blank tags, keeping order."""
    if tags is None:
        return ()
    if isinstance(tags, str):
        tags = tags.split(",")
    seen: dict[str, None] = {}
    for tag in tags:
        if not isinstance(tag, str):
            raise ValidationError("each tag must be a string")
        cleaned = tag.strip().lower()
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Link:
    """A single saved bookmark."""

    id: int
    url: str
    title: str
    tags: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "tags": list(self.tags),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Link":
        raw_tags = row["tags"] or ""
        tags = tuple(t for t in raw_tags.split(",") if t)
        return cls(
            id=row["id"],
            url=row["url"],
            title=row["title"],
            tags=tags,
            created_at=row["created_at"],
        )


class LinkStore:
    """A tiny bookmark store backed by SQLite.

    Pass ``":memory:"`` (the default) for an ephemeral store, or a file path to
    persist links between runs.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS links (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    url        TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    tags       TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )

    def add(self, url: str, title: str = "", tags: Iterable[str] = ()) -> Link:
        """Validate and insert a link, returning the stored record."""
        clean_url = normalize_url(url)
        clean_title = (title or "").strip()
        if not clean_title:
            # Fall back to the host so a bookmark always has a readable label.
            clean_title = urlparse(clean_url).netloc
        clean_tags = normalize_tags(tags)
        created_at = _utc_now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO links (url, title, tags, created_at) VALUES (?, ?, ?, ?)",
                (clean_url, clean_title, ",".join(clean_tags), created_at),
            )
            link_id = cursor.lastrowid
        return Link(link_id, clean_url, clean_title, clean_tags, created_at)

    def get(self, link_id: int) -> Optional[Link]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM links WHERE id = ?", (link_id,)
            ).fetchone()
        return Link.from_row(row) if row else None

    def list(self, tag: Optional[str] = None, query: Optional[str] = None) -> list[Link]:
        """Return links, newest first, optionally filtered by tag and/or text."""
        sql = "SELECT * FROM links"
        clauses: list[str] = []
        params: list[object] = []
        if tag:
            # Tags are stored comma-wrapped so a LIKE match is exact per tag;
            # LIKE metacharacters in the tag itself are escaped.
            clauses.append("',' || tags || ',' LIKE ? ESCAPE '\\'")
            needle = tag.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params.append(f"%,{needle},%")
        if query:
            clauses.append("(LOWER(title) LIKE ? OR LOWER(url) LIKE ?)")
            needle = f"%{query.strip().lower()}%"
            params.extend([needle, needle])
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Link.from_row(row) for row in rows]

    def delete(self, link_id: int) -> bool:
        """Delete a link; return ``True`` when a row was removed."""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM links WHERE id = ?", (link_id,))
            return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM links").fetchone()
        return int(row["n"])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
