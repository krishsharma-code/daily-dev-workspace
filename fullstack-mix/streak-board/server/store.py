"""SQLite storage for habits and their check-ins.

Two tables, no ORM: ``habits`` (one row per habit, weekdays stored as a sorted
comma-separated list) and ``checkins`` (one row per habit *day*, with the day as
part of the primary key). That primary key is the whole idempotency story --
checking in twice on the same day is an ``INSERT OR IGNORE`` that reports
``created: False`` instead of duplicating history or throwing at the caller.

Use ``":memory:"`` for tests and demos, or a file path to keep habits across
restarts.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import date, datetime, timezone
from typing import Optional

from streak import (
    StreakError,
    format_day,
    normalise_weekdays,
    parse_day,
    streak_summary,
)

__all__ = [
    "HabitStore",
    "MAX_NAME",
    "MAX_NOTE",
    "StoreError",
    "slugify",
]

MAX_NAME = 80
MAX_NOTE = 280
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS habits (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    weekdays    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkins (
    habit_id    TEXT NOT NULL REFERENCES habits(id) ON DELETE CASCADE,
    day         TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    PRIMARY KEY (habit_id, day)
);

CREATE INDEX IF NOT EXISTS checkins_by_habit ON checkins(habit_id, day);
"""


class StoreError(ValueError):
    """Raised for input the store refuses (bad names, unknown ids, ...)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    """Turn a habit name into a stable, URL-safe id."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:64].strip("-")
    if not slug:
        raise StoreError("habit name must contain at least one letter or digit")
    return slug


def _clean_name(name: object) -> str:
    if not isinstance(name, str):
        raise StoreError("name must be a string")
    cleaned = " ".join(name.split())
    if not cleaned:
        raise StoreError("name must not be blank")
    if len(cleaned) > MAX_NAME:
        raise StoreError(f"name must be at most {MAX_NAME} characters")
    return cleaned


def _clean_note(note: object) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise StoreError("note must be a string")
    cleaned = " ".join(note.split())
    if len(cleaned) > MAX_NOTE:
        raise StoreError(f"note must be at most {MAX_NOTE} characters")
    return cleaned


class HabitStore:
    """Habits and their check-ins, backed by a single SQLite connection."""

    def __init__(self, path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "HabitStore":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- habits ------------------------------------------------------------

    def create_habit(self, name: object, weekdays: object = None) -> dict:
        """Create a habit. The id is a slug of the name and must be unique."""
        clean = _clean_name(name)
        try:
            days = normalise_weekdays(weekdays)
        except StreakError as exc:
            raise StoreError(str(exc)) from exc
        habit_id = slugify(clean)
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM habits WHERE id = ?", (habit_id,)
            ).fetchone()
            if exists:
                raise StoreError(f"a habit called {habit_id!r} already exists")
            self._conn.execute(
                "INSERT INTO habits (id, name, weekdays, created_at)"
                " VALUES (?, ?, ?, ?)",
                (habit_id, clean, ",".join(str(day) for day in days), _now()),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM habits WHERE id = ?", (habit_id,)
            ).fetchone()
        return _habit_row(row)

    def list_habits(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM habits ORDER BY created_at, id"
            ).fetchall()
        return [_habit_row(row) for row in rows]

    def get_habit(self, habit_id: str) -> Optional[dict]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM habits WHERE id = ?", (habit_id,)
            ).fetchone()
        return _habit_row(row) if row else None

    def delete_habit(self, habit_id: str) -> bool:
        """Delete a habit and, by cascade, all of its check-ins."""
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM habits WHERE id = ?", (habit_id,)
            )
            self._conn.commit()
        return cursor.rowcount > 0

    # -- check-ins ---------------------------------------------------------

    def check_in(
        self,
        habit_id: str,
        day: object = None,
        note: object = "",
        today: object = None,
    ) -> dict:
        """Record a completed day. Repeating it is a no-op, not an error.

        A check-in may be backfilled, but never dated in the future: a streak
        that can be pre-filled says nothing about whether anything was done.
        """
        habit = self.get_habit(habit_id)
        if habit is None:
            raise StoreError(f"unknown habit {habit_id!r}")
        today_day = parse_day(today) if today is not None else date.today()
        try:
            day_value = parse_day(day) if day is not None else today_day
        except StreakError as exc:
            raise StoreError(str(exc)) from exc
        if day_value > today_day:
            raise StoreError("cannot check in for a day in the future")
        clean_note = _clean_note(note)

        with self._lock:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO checkins (habit_id, day, note, created_at)"
                " VALUES (?, ?, ?, ?)",
                (habit_id, format_day(day_value), clean_note, _now()),
            )
            created = cursor.rowcount > 0
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM checkins WHERE habit_id = ? AND day = ?",
                (habit_id, format_day(day_value)),
            ).fetchone()
        entry = _checkin_row(row)
        return {"created": created, "checkin": entry}

    def undo_check_in(self, habit_id: str, day: object) -> bool:
        """Remove a check-in. Returns whether a row was actually removed."""
        try:
            day_text = format_day(parse_day(day))
        except StreakError as exc:
            raise StoreError(str(exc)) from exc
        if self.get_habit(habit_id) is None:
            raise StoreError(f"unknown habit {habit_id!r}")
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM checkins WHERE habit_id = ? AND day = ?",
                (habit_id, day_text),
            )
            self._conn.commit()
        return cursor.rowcount > 0

    def checkins(self, habit_id: str) -> list[dict]:
        """All check-ins for a habit, oldest first."""
        if self.get_habit(habit_id) is None:
            raise StoreError(f"unknown habit {habit_id!r}")
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM checkins WHERE habit_id = ? ORDER BY day",
                (habit_id,),
            ).fetchall()
        return [_checkin_row(row) for row in rows]

    def checkin_days(self, habit_id: str) -> list[str]:
        return [entry["day"] for entry in self.checkins(habit_id)]

    # -- views -------------------------------------------------------------

    def habit_view(self, habit: dict, today: object = None) -> dict:
        """A habit plus its streak numbers and full history, as the API returns it.

        The history travels with the habit so the client can paint a calendar
        without one request per day -- a personal habit log is small, and one
        round trip beats thirty.
        """
        today_day = parse_day(today) if today is not None else date.today()
        checked = self.checkin_days(habit["id"])
        summary = streak_summary(checked, today_day, habit["weekdays"])
        return {**habit, "days": checked, "summary": summary}

    def board(self, today: object = None) -> dict:
        """Every habit with its summary -- the payload the client renders."""
        today_day = parse_day(today) if today is not None else date.today()
        habits = [
            self.habit_view(habit, today_day) for habit in self.list_habits()
        ]
        done = sum(1 for habit in habits if habit["summary"]["done_today"])
        due = sum(1 for habit in habits if habit["summary"]["scheduled_today"])
        return {
            "today": format_day(today_day),
            "habits": habits,
            "counts": {"habits": len(habits), "done_today": done, "due_today": due},
        }


# ---------------------------------------------------------------------------
# Row helpers
# ---------------------------------------------------------------------------


def _habit_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "weekdays": [int(part) for part in row["weekdays"].split(",") if part],
        "created_at": row["created_at"],
    }


def _checkin_row(row: sqlite3.Row) -> dict:
    return {
        "habit_id": row["habit_id"],
        "day": row["day"],
        "note": row["note"],
        "created_at": row["created_at"],
    }
