"""SQLite storage for the Webhook Inbox.

The store owns every bit of persistence: named bins (inboxes) and the raw
events captured into them. It knows nothing about HTTP, which keeps it easy to
test on its own.

Design notes
------------
* Bins are auto-created the first time an event arrives for them, so a webhook
  sender never has to register anything up front.
* Bodies are stored as bytes. A body larger than :data:`MAX_BODY_BYTES` is
  truncated on the way in and flagged, so one chatty sender cannot fill the
  database.
* Header names are lowercased and values coerced to ``str`` on the way in.
* One connection is shared behind a lock, because the HTTP server is
  multi-threaded.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

__all__ = [
    "DEFAULT_LIMIT",
    "MAX_BODY_BYTES",
    "MAX_LIMIT",
    "Event",
    "InboxStore",
    "ValidationError",
    "normalise_bin_name",
]

MAX_BODY_BYTES = 256 * 1024
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Lowercase letters, digits, dash and underscore; must start with a letter or
# digit so a bin name is always safe inside a URL path segment.
_BIN_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bins (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bin         TEXT NOT NULL,
    method      TEXT NOT NULL,
    path        TEXT NOT NULL,
    headers     TEXT NOT NULL,
    body        BLOB NOT NULL,
    body_size   INTEGER NOT NULL,
    truncated   INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_by_bin ON events(bin, id DESC);
"""


class ValidationError(ValueError):
    """Raised when caller-supplied input is unusable."""


def _utcnow() -> str:
    """Timestamp in ISO-8601 UTC, second precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalise_bin_name(name: Any) -> str:
    """Return a canonical bin name or raise :class:`ValidationError`."""
    if not isinstance(name, str):
        raise ValidationError("bin name must be a string")
    candidate = name.strip().lower()
    if not candidate:
        raise ValidationError("bin name is required")
    if not _BIN_NAME_RE.match(candidate):
        raise ValidationError(
            "bin name must be 1-64 chars of lowercase letters, digits, '-' or '_' "
            "and start with a letter or digit"
        )
    return candidate


def _normalise_headers(headers: Optional[dict]) -> dict:
    """Lowercase keys, stringify values, keep the first value for repeats."""
    cleaned: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key is None:
            continue
        name = str(key).strip().lower()
        if not name:
            continue
        if isinstance(value, (list, tuple)):
            text = ", ".join(str(item) for item in value)
        else:
            text = str(value)
        cleaned[name] = text
    return cleaned


@dataclass(frozen=True)
class Event:
    """One captured webhook delivery."""

    id: int
    bin: str
    method: str
    path: str
    headers: dict
    body: bytes
    body_size: int
    truncated: bool
    received_at: str

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def body_view(self) -> tuple[str, str]:
        """Return ``(text, encoding)`` for display: utf-8 when possible."""
        try:
            return self.body.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            return base64.b64encode(self.body).decode("ascii"), "base64"

    def to_dict(self, include_body: bool = True) -> dict:
        text, encoding = self.body_view()
        data = {
            "id": self.id,
            "bin": self.bin,
            "method": self.method,
            "path": self.path,
            "headers": self.headers,
            "content_type": self.content_type,
            "body_size": self.body_size,
            "truncated": self.truncated,
            "received_at": self.received_at,
        }
        if include_body:
            data["body"] = text
            data["body_encoding"] = encoding
        else:
            data["body_preview"] = text[:200]
        return data


class InboxStore:
    """Persistence for bins and captured events."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- bins --------------------------------------------------------------

    def ensure_bin(self, name: Any) -> tuple[dict, bool]:
        """Create ``name`` if needed. Returns ``(bin_info, created)``."""
        clean = normalise_bin_name(name)
        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO bins (name, created_at) VALUES (?, ?)",
                (clean, _utcnow()),
            )
            created = cursor.rowcount == 1
            self._conn.commit()
            row = self._conn.execute(
                "SELECT name, created_at FROM bins WHERE name = ?", (clean,)
            ).fetchone()
        return {"name": row["name"], "created_at": row["created_at"]}, created

    def list_bins(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT b.name,
                       b.created_at,
                       COUNT(e.id) AS events,
                       MAX(e.received_at) AS last_received_at
                FROM bins AS b
                LEFT JOIN events AS e ON e.bin = b.name
                GROUP BY b.name, b.created_at
                ORDER BY b.name
                """
            ).fetchall()
        return [
            {
                "name": row["name"],
                "created_at": row["created_at"],
                "events": row["events"],
                "last_received_at": row["last_received_at"],
            }
            for row in rows
        ]

    def get_bin(self, name: Any) -> Optional[dict]:
        clean = normalise_bin_name(name)
        for info in self.list_bins():
            if info["name"] == clean:
                return info
        return None

    def delete_bin(self, name: Any) -> bool:
        """Delete a bin and every event inside it."""
        clean = normalise_bin_name(name)
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE bin = ?", (clean,))
            cursor = self._conn.execute("DELETE FROM bins WHERE name = ?", (clean,))
            self._conn.commit()
        return cursor.rowcount == 1

    # -- events ------------------------------------------------------------

    def add_event(
        self,
        bin: Any,
        method: str,
        path: str,
        headers: Optional[dict] = None,
        body: bytes = b"",
    ) -> Event:
        """Store one captured delivery, creating the bin when it is new."""
        clean = normalise_bin_name(bin)
        payload = body or b""
        if isinstance(payload, str):  # defensive: callers should pass bytes
            payload = payload.encode("utf-8")
        size = len(payload)
        truncated = size > MAX_BODY_BYTES
        stored = payload[:MAX_BODY_BYTES]
        method = (method or "GET").upper()
        path = path or "/"

        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO bins (name, created_at) VALUES (?, ?)",
                (clean, _utcnow()),
            )
            cursor = self._conn.execute(
                """
                INSERT INTO events
                    (bin, method, path, headers, body, body_size, truncated, received_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    clean,
                    method,
                    path,
                    json.dumps(_normalise_headers(headers), sort_keys=True),
                    stored,
                    size,
                    int(truncated),
                    _utcnow(),
                ),
            )
            self._conn.commit()
            event_id = cursor.lastrowid
            row = self._conn.execute(
                "SELECT * FROM events WHERE id = ?", (event_id,)
            ).fetchone()
        return _row_to_event(row)

    def list_events(
        self,
        bin: Any = None,
        limit: int = DEFAULT_LIMIT,
        before_id: Optional[int] = None,
    ) -> list[Event]:
        """Newest-first events, optionally limited to one bin."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []
        if bin is not None:
            clauses.append("bin = ?")
            params.append(normalise_bin_name(bin))
        if before_id is not None:
            clauses.append("id < ?")
            params.append(int(before_id))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [_row_to_event(row) for row in rows]

    def get_event(self, event_id: int) -> Optional[Event]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM events WHERE id = ?", (int(event_id),)
            ).fetchone()
        return _row_to_event(row) if row is not None else None

    def delete_event(self, event_id: int) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM events WHERE id = ?", (int(event_id),)
            )
            self._conn.commit()
        return cursor.rowcount == 1

    def clear_bin(self, name: Any) -> int:
        """Drop every event in a bin but keep the bin itself."""
        clean = normalise_bin_name(name)
        with self._lock:
            cursor = self._conn.execute("DELETE FROM events WHERE bin = ?", (clean,))
            self._conn.commit()
        return cursor.rowcount

    # -- reporting ---------------------------------------------------------

    def count_events(self, bin: Any = None) -> int:
        with self._lock:
            if bin is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM events WHERE bin = ?",
                    (normalise_bin_name(bin),),
                ).fetchone()
        return int(row["n"])

    def count_bins(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM bins").fetchone()
        return int(row["n"])


def _row_to_event(row: sqlite3.Row) -> Event:
    raw_headers = json.loads(row["headers"]) if row["headers"] else {}
    body = row["body"] or b""
    if isinstance(body, str):  # pragma: no cover - sqlite3 always returns bytes
        body = body.encode("utf-8")
    return Event(
        id=int(row["id"]),
        bin=row["bin"],
        method=row["method"],
        path=row["path"],
        headers={str(k).lower(): str(v) for k, v in raw_headers.items()},
        body=body,
        body_size=int(row["body_size"]),
        truncated=bool(row["truncated"]),
        received_at=row["received_at"],
    )
