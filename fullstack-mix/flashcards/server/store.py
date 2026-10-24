"""SQLite-backed storage for the Flashcards app.

The store owns persistence and validation; the scheduling maths lives in
:mod:`srs`. Like the rest of the project it is standard-library only and safe
to share across threads, which is what :mod:`http.server` needs (it handles
each request on its own thread).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from srs import CardState, sm2_update, ValidationError

__all__ = ["Card", "CardStore", "ValidationError", "DEFAULT_DECK", "normalize_deck", "normalize_text"]

DEFAULT_DECK = "default"

_MAX_FIELD_LEN = 500
_MAX_DECK_LEN = 60


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _as_utc(value: datetime) -> datetime:
    """Coerce a datetime to UTC; a naive value is assumed to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def normalize_deck(raw: str) -> str:
    """Return a cleaned deck name, falling back to ``default`` when blank."""
    if raw is None:
        return DEFAULT_DECK
    if not isinstance(raw, str):
        raise ValidationError("deck must be a string")
    cleaned = " ".join(raw.split())
    if not cleaned:
        return DEFAULT_DECK
    if len(cleaned) > _MAX_DECK_LEN:
        raise ValidationError(f"deck must be at most {_MAX_DECK_LEN} characters")
    return cleaned


def normalize_text(field: str, raw: str) -> str:
    """Validate and trim a required free-text field (``front`` or ``back``)."""
    if not isinstance(raw, str):
        raise ValidationError(f"{field} must be a string")
    cleaned = raw.strip()
    if not cleaned:
        raise ValidationError(f"{field} is required")
    if len(cleaned) > _MAX_FIELD_LEN:
        raise ValidationError(f"{field} must be at most {_MAX_FIELD_LEN} characters")
    return cleaned


@dataclass(frozen=True)
class Card:
    """A single flashcard plus its current scheduling state."""

    id: int
    deck: str
    front: str
    back: str
    ease: float
    interval_days: int
    repetitions: int
    lapses: int
    due: str
    created_at: str

    @property
    def state(self) -> CardState:
        return CardState(
            ease=self.ease,
            interval_days=self.interval_days,
            repetitions=self.repetitions,
            lapses=self.lapses,
            due=datetime.fromisoformat(self.due),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "deck": self.deck,
            "front": self.front,
            "back": self.back,
            "ease": round(self.ease, 2),
            "interval_days": self.interval_days,
            "repetitions": self.repetitions,
            "lapses": self.lapses,
            "due": self.due,
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Card":
        return cls(
            id=row["id"],
            deck=row["deck"],
            front=row["front"],
            back=row["back"],
            ease=row["ease"],
            interval_days=row["interval_days"],
            repetitions=row["repetitions"],
            lapses=row["lapses"],
            due=row["due"],
            created_at=row["created_at"],
        )


class CardStore:
    """A small flashcard store backed by SQLite.

    Pass ``":memory:"`` (the default) for an ephemeral store, or a file path to
    persist cards between runs. Every method accepts an optional ``now`` so the
    clock can be injected in tests.
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
                CREATE TABLE IF NOT EXISTS cards (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    deck          TEXT NOT NULL,
                    front         TEXT NOT NULL,
                    back          TEXT NOT NULL,
                    ease          REAL NOT NULL,
                    interval_days INTEGER NOT NULL,
                    repetitions   INTEGER NOT NULL,
                    lapses        INTEGER NOT NULL,
                    due           TEXT NOT NULL,
                    created_at    TEXT NOT NULL
                )
                """
            )

    # -- writes ---------------------------------------------------------------

    def add(self, front: str, back: str, deck: str = DEFAULT_DECK, now: Optional[datetime] = None) -> Card:
        """Validate and insert a card, due immediately."""
        clean_deck = normalize_deck(deck)
        clean_front = normalize_text("front", front)
        clean_back = normalize_text("back", back)
        moment = _as_utc(now) if now is not None else _utc_now()
        due = moment.replace(microsecond=0).isoformat()
        created_at = due
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO cards
                    (deck, front, back, ease, interval_days, repetitions, lapses, due, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (clean_deck, clean_front, clean_back, 2.5, 0, 0, 0, due, created_at),
            )
            card_id = cursor.lastrowid
        return Card(card_id, clean_deck, clean_front, clean_back, 2.5, 0, 0, 0, due, created_at)

    def review(self, card_id: int, quality: int, now: Optional[datetime] = None) -> Optional[Card]:
        """Apply a review to a card and persist its new schedule.

        Returns the updated card, or ``None`` when the id does not exist.
        Raises :class:`ValidationError` for an out-of-range quality.
        """
        card = self.get(card_id)
        if card is None:
            return None
        moment = _as_utc(now) if now is not None else _utc_now()
        new_state = sm2_update(card.state, quality, moment)
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE cards
                   SET ease = ?, interval_days = ?, repetitions = ?, lapses = ?, due = ?
                 WHERE id = ?
                """,
                (
                    new_state.ease,
                    new_state.interval_days,
                    new_state.repetitions,
                    new_state.lapses,
                    new_state.due.replace(microsecond=0).isoformat(),
                    card_id,
                ),
            )
        return self.get(card_id)

    def delete(self, card_id: int) -> bool:
        """Delete a card; return ``True`` when a row was removed."""
        with self._lock, self._conn:
            cursor = self._conn.execute("DELETE FROM cards WHERE id = ?", (card_id,))
            return cursor.rowcount > 0

    # -- reads ----------------------------------------------------------------

    def get(self, card_id: int) -> Optional[Card]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return Card.from_row(row) if row else None

    def list(self, deck: Optional[str] = None) -> list[Card]:
        """Return cards, oldest first, optionally restricted to one deck."""
        sql = "SELECT * FROM cards"
        params: list[object] = []
        if deck:
            sql += " WHERE deck = ?"
            params.append(normalize_deck(deck))
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Card.from_row(row) for row in rows]

    def due(self, deck: Optional[str] = None, now: Optional[datetime] = None) -> list[Card]:
        """Return cards whose due date is at or before ``now``, oldest first."""
        moment = _as_utc(now) if now is not None else _utc_now()
        stamp = moment.replace(microsecond=0).isoformat()
        sql = "SELECT * FROM cards WHERE due <= ?"
        params: list[object] = [stamp]
        if deck:
            sql += " AND deck = ?"
            params.append(normalize_deck(deck))
        sql += " ORDER BY due ASC, id ASC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [Card.from_row(row) for row in rows]

    def decks(self, now: Optional[datetime] = None) -> list[dict]:
        """Summarise every deck with its total and due-now counts."""
        moment = _as_utc(now) if now is not None else _utc_now()
        stamp = moment.replace(microsecond=0).isoformat()
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT deck,
                       COUNT(*) AS total,
                       SUM(CASE WHEN due <= ? THEN 1 ELSE 0 END) AS due
                  FROM cards
              GROUP BY deck
              ORDER BY deck ASC
                """,
                (stamp,),
            ).fetchall()
        return [
            {"deck": row["deck"], "total": int(row["total"]), "due": int(row["due"] or 0)}
            for row in rows
        ]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM cards").fetchone()
        return int(row["n"])

    def due_count(self, now: Optional[datetime] = None) -> int:
        return len(self.due(now=now))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
