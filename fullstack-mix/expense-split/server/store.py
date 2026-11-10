"""SQLite storage for expense groups, their members and their bills.

A group is identified by a slug (``"goa-trip"``) so it can be typed into a URL
or shared with the group. Amounts are stored as integer paise; the split of each
bill is stored as JSON mapping a member to their share in paise, so a stored
expense always adds up on its own and never depends on today's split rules.

Use ``":memory:"`` for tests and demos, or a file path to keep data across
restarts.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from splitter import (
    ValidationError,
    balances_for,
    format_amount,
    normalise_names,
    parse_amount,
    simplify_debts,
    split as make_split,
)

__all__ = ["ExpenseStore", "MAX_DESCRIPTION", "ValidationError"]

MAX_DESCRIPTION = 200
MAX_GROUP_NAME = 80
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS groups (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    members     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id    TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    payer       TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    split_type  TEXT NOT NULL,
    shares      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS expenses_by_group ON expenses(group_id, id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    """Turn a group name into a stable slug id."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:64].strip("-")
    if not slug:
        raise ValidationError("group name must contain at least one letter or digit")
    return slug


class ExpenseStore:
    """Thin, validated layer over the two tables above."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- groups ------------------------------------------------------------

    def create_group(self, name: object, members: object) -> dict[str, Any]:
        if not isinstance(name, str):
            raise ValidationError("group name must be a string")
        name = name.strip()
        if not name:
            raise ValidationError("group name must not be blank")
        if len(name) > MAX_GROUP_NAME:
            raise ValidationError(f"group name must be at most {MAX_GROUP_NAME} characters")
        people = normalise_names(members, what="members")
        if len(people) < 2:
            raise ValidationError("a group needs at least two members")

        group_id = slugify(name)
        record = {
            "id": group_id,
            "name": name,
            "members": list(people),
            "created_at": _now(),
        }
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO groups (id, name, members, created_at) VALUES (?, ?, ?, ?)",
                    (group_id, name, json.dumps(list(people)), record["created_at"]),
                )
            except sqlite3.IntegrityError:
                raise ValidationError(f"a group called '{group_id}' already exists") from None
            self._db.commit()
        return record

    def list_groups(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id, name, members, created_at FROM groups ORDER BY created_at, id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "members": json.loads(row["members"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_group(self, group_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, name, members, created_at FROM groups WHERE id = ?",
                (group_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "name": row["name"],
            "members": json.loads(row["members"]),
            "created_at": row["created_at"],
        }

    def delete_group(self, group_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute("DELETE FROM groups WHERE id = ?", (group_id,))
            self._db.execute("DELETE FROM expenses WHERE group_id = ?", (group_id,))
            self._db.commit()
        return cursor.rowcount > 0

    # -- expenses ----------------------------------------------------------

    def add_expense(
        self,
        group_id: str,
        description: object,
        payer: object,
        amount: object,
        split_type: str = "equal",
        participants: Optional[Sequence[str]] = None,
        shares: Optional[Mapping[str, object]] = None,
        weights: Optional[Mapping[str, object]] = None,
    ) -> dict[str, Any]:
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(group_id)
        members = group["members"]

        if not isinstance(description, str):
            raise ValidationError("description must be a string")
        description = description.strip()
        if not description:
            raise ValidationError("description must not be blank")
        if len(description) > MAX_DESCRIPTION:
            raise ValidationError(f"description must be at most {MAX_DESCRIPTION} characters")

        if not isinstance(payer, str) or payer.strip() not in members:
            raise ValidationError(f"payer must be one of: {', '.join(members)}")
        payer = payer.strip()

        total = parse_amount(amount)
        if total <= 0:
            raise ValidationError("amount must be greater than zero")

        kind = (split_type or "equal").strip().lower()
        if kind in ("exact",) and shares is None:
            raise ValidationError("exact splits need a 'shares' map")
        if kind in ("weights", "weighted", "percent", "percentage", "shares"):
            if weights is None and shares is None:
                raise ValidationError("weighted splits need a 'weights' map")
            kind = "weights"
        if kind == "equal" and participants is None:
            participants = members

        allocation = make_split(
            total,
            kind,
            participants=participants,
            shares=shares,
            weights=weights,
        )
        strangers = sorted(set(allocation) - set(members))
        if strangers:
            raise ValidationError(
                f"not in this group: {', '.join(strangers)}"
            )
        if sum(allocation.values()) != total:
            raise ValidationError("split does not add up to the amount paid")

        created_at = _now()
        with self._lock:
            cursor = self._db.execute(
                """INSERT INTO expenses
                       (group_id, description, payer, amount_paise, split_type, shares, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    group_id,
                    description,
                    payer,
                    total,
                    "weights" if kind == "weights" else kind,
                    json.dumps(allocation, sort_keys=True),
                    created_at,
                ),
            )
            self._db.commit()
            expense_id = int(cursor.lastrowid)
        return {
            "id": expense_id,
            "group_id": group_id,
            "description": description,
            "payer": payer,
            "amount_paise": total,
            "amount": format_amount(total),
            "split_type": "weights" if kind == "weights" else kind,
            "shares": allocation,
            "created_at": created_at,
        }

    def list_expenses(self, group_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                """SELECT id, description, payer, amount_paise, split_type, shares, created_at
                     FROM expenses WHERE group_id = ? ORDER BY id""",
                (group_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "group_id": group_id,
                "description": row["description"],
                "payer": row["payer"],
                "amount_paise": row["amount_paise"],
                "amount": format_amount(row["amount_paise"]),
                "split_type": row["split_type"],
                "shares": json.loads(row["shares"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def delete_expense(self, group_id: str, expense_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM expenses WHERE group_id = ? AND id = ?",
                (group_id, expense_id),
            )
            self._db.commit()
        return cursor.rowcount > 0

    # -- derived state -----------------------------------------------------

    def balances(self, group_id: str) -> dict[str, int]:
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(group_id)
        return balances_for(self.list_expenses(group_id), group["members"])

    def settlement(self, group_id: str) -> dict[str, Any]:
        """Balances plus the shortest sensible plan to settle up."""
        group = self.get_group(group_id)
        if group is None:
            raise KeyError(group_id)
        expenses = self.list_expenses(group_id)
        balances = balances_for(expenses, group["members"])
        transfers = simplify_debts(balances)
        total = sum(expense["amount_paise"] for expense in expenses)
        return {
            "group_id": group_id,
            "total_paise": total,
            "total": format_amount(total),
            "balances": {
                name: {"paise": amount, "amount": format_amount(amount)}
                for name, amount in balances.items()
            },
            "settlements": transfers,
        }
