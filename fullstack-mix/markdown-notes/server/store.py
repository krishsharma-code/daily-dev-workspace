"""SQLite-backed storage for Markdown notes.

The store owns validation too, so the HTTP layer stays a thin adapter and the
same rules apply to every caller (including tests).

    >>> store = NoteStore(":memory:")
    >>> note = store.add(body="# Hello")
    >>> note.title
    'Hello'
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

__all__ = ["Note", "NoteStore", "ValidationError", "MAX_TITLE", "MAX_BODY"]

MAX_TITLE = 200
MAX_BODY = 100_000
DEFAULT_TITLE = "Untitled note"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_updated_at ON notes (updated_at DESC);
"""


class ValidationError(ValueError):
    """Raised when a note fails validation."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class Note:
    id: int
    title: str
    body: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def derive_title(body: str) -> str:
    """Pick a sensible title from the body when the author gives none."""
    for line in (body or "").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:MAX_TITLE]
    return DEFAULT_TITLE


def _check_text(value: object, name: str, limit: int, required: bool) -> str:
    if value is None:
        if required:
            raise ValidationError(f"{name} is required")
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if "\x00" in value:
        raise ValidationError(f"{name} must not contain NUL bytes")
    if len(value) > limit:
        raise ValidationError(f"{name} must be at most {limit} characters")
    if required and not value.strip():
        raise ValidationError(f"{name} must not be blank")
    return value


class NoteStore:
    """A tiny notes repository backed by SQLite."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- reads ---------------------------------------------------------
    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()
        return int(row["n"])

    def list(self, query: Optional[str] = None) -> list:
        sql = "SELECT * FROM notes"
        params: list = []
        needle = query.strip() if query else ""
        if needle:
            # Escape LIKE wildcards so a search for "50%" or "a_b" is literal.
            escaped = (
                needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            sql += " WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\'"
            params = [f"%{escaped}%", f"%{escaped}%"]
        sql += " ORDER BY updated_at DESC, id DESC"
        return [self._row(row) for row in self._conn.execute(sql, params)]

    def get(self, note_id: int) -> Optional[Note]:
        row = self._conn.execute(
            "SELECT * FROM notes WHERE id = ?", (note_id,)
        ).fetchone()
        return self._row(row) if row else None

    # -- writes --------------------------------------------------------
    def add(self, title: str = "", body: str = "") -> Note:
        body = _check_text(body, "body", MAX_BODY, required=False)
        title = _check_text(title, "title", MAX_TITLE, required=False).strip()
        if not title:
            title = derive_title(body)
        stamp = _now()
        cursor = self._conn.execute(
            "INSERT INTO notes (title, body, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (title, body, stamp, stamp),
        )
        self._conn.commit()
        note = self.get(int(cursor.lastrowid))
        assert note is not None  # just inserted
        return note

    def update(
        self,
        note_id: int,
        title: Optional[str] = None,
        body: Optional[str] = None,
    ) -> Optional[Note]:
        current = self.get(note_id)
        if current is None:
            return None
        if title is None and body is None:
            raise ValidationError("nothing to update")

        new_body = current.body if body is None else _check_text(
            body, "body", MAX_BODY, required=False
        )
        if title is None:
            new_title = current.title
        else:
            new_title = _check_text(title, "title", MAX_TITLE, required=False).strip()
            if not new_title:
                new_title = derive_title(new_body)

        self._conn.execute(
            "UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ?",
            (new_title, new_body, _now(), note_id),
        )
        self._conn.commit()
        return self.get(note_id)

    def delete(self, note_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    def close(self) -> None:
        self._conn.close()

    # -- helpers -------------------------------------------------------
    @staticmethod
    def _row(row: sqlite3.Row) -> Note:
        return Note(
            id=int(row["id"]),
            title=row["title"],
            body=row["body"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
