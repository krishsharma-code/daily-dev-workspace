"""Feature flag storage and deterministic evaluation.

Flags live in SQLite. Evaluation is a pure, deterministic function of the flag
key and the user id, so the same user always lands in the same bucket for the
same flag - no database write, no random number, no state to keep in sync.

Run against an in-memory database (``":memory:"``) for tests and demos, or a
file path to keep flags across restarts.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

__all__ = [
    "BUCKETS",
    "Flag",
    "FlagStore",
    "ValidationError",
    "bucket_for",
    "REASON_ALLOWLIST",
    "REASON_DISABLED",
    "REASON_OUTSIDE_ROLLOUT",
    "REASON_ROLLOUT",
]

# Fine-grained bucketing keeps small rollouts (1%, 0.5%) honest.
BUCKETS = 10_000

KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_DESCRIPTION = 280
MAX_ALLOWLIST = 500
MAX_USER_ID = 128

REASON_DISABLED = "disabled"
REASON_ALLOWLIST = "allowlist"
REASON_ROLLOUT = "rollout"
REASON_OUTSIDE_ROLLOUT = "outside_rollout"


class ValidationError(ValueError):
    """Raised when a flag definition or an evaluation request is malformed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def normalise_key(key: object) -> str:
    """Return a canonical flag key, or raise :class:`ValidationError`."""
    if not isinstance(key, str):
        raise ValidationError("key must be a string")
    cleaned = key.strip().lower()
    if not KEY_PATTERN.match(cleaned):
        raise ValidationError(
            "key must be 1-64 characters of lowercase letters, digits, '.', '-' or '_'"
        )
    return cleaned


def normalise_user_id(user_id: object) -> str:
    """Return a canonical user id, or raise :class:`ValidationError`."""
    if not isinstance(user_id, str):
        raise ValidationError("user_id must be a string")
    cleaned = user_id.strip()
    if not cleaned:
        raise ValidationError("user_id must not be empty")
    if len(cleaned) > MAX_USER_ID:
        raise ValidationError(f"user_id must be at most {MAX_USER_ID} characters")
    return cleaned


def normalise_description(description: object) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise ValidationError("description must be a string")
    cleaned = description.strip()
    if len(cleaned) > MAX_DESCRIPTION:
        raise ValidationError(
            f"description must be at most {MAX_DESCRIPTION} characters"
        )
    return cleaned


def normalise_rollout(rollout: object) -> int:
    """Accept ``0``-``100`` as an int (``bool`` is rejected)."""
    if isinstance(rollout, bool) or not isinstance(rollout, int):
        raise ValidationError("rollout must be an integer between 0 and 100")
    if not 0 <= rollout <= 100:
        raise ValidationError("rollout must be between 0 and 100")
    return rollout


def normalise_allowlist(allowlist: object) -> tuple[str, ...]:
    """Normalise an allowlist, dropping duplicates but keeping order."""
    if allowlist is None:
        return ()
    if isinstance(allowlist, str):
        # Convenience for the query string and hand-written JSON.
        allowlist = [part for part in allowlist.replace("\n", ",").split(",")]
    if not isinstance(allowlist, (list, tuple)):
        raise ValidationError("allowlist must be a list of user ids")
    seen: dict[str, None] = {}
    for item in allowlist:
        if not isinstance(item, str):
            raise ValidationError("allowlist entries must be strings")
        cleaned = item.strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_USER_ID:
            raise ValidationError(
                f"allowlist entries must be at most {MAX_USER_ID} characters"
            )
        seen.setdefault(cleaned, None)
    if len(seen) > MAX_ALLOWLIST:
        raise ValidationError(
            f"allowlist must hold at most {MAX_ALLOWLIST} user ids"
        )
    return tuple(seen)


def normalise_enabled(enabled: object) -> bool:
    if isinstance(enabled, bool):
        return enabled
    raise ValidationError("enabled must be true or false")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def bucket_for(key: str, user_id: str) -> int:
    """Return a stable bucket in ``0 .. BUCKETS - 1`` for a key/user pair.

    The hash is over ``"<key>:<user_id>"`` so one user is spread differently
    across flags (rollouts do not clump onto the same people) while staying
    fixed for each individual flag.
    """
    digest = hashlib.sha256(f"{key}:{user_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % BUCKETS


@dataclass(frozen=True)
class Flag:
    key: str
    description: str = ""
    enabled: bool = False
    rollout: int = 0
    allowlist: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "description": self.description,
            "enabled": self.enabled,
            "rollout": self.rollout,
            "allowlist": list(self.allowlist),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def evaluate(self, user_id: str) -> dict:
        """Decide whether ``user_id`` sees this flag.

        Order matters: a switched-off flag is off for everyone, including
        allowlisted users, because the switch is the kill switch. Inside an
        enabled flag, the allowlist wins over the rollout percentage.
        """
        if not self.enabled:
            return self._decision(user_id, False, REASON_DISABLED, bucket_for(self.key, user_id))
        if user_id in self.allowlist:
            return self._decision(user_id, True, REASON_ALLOWLIST, bucket_for(self.key, user_id))
        bucket = bucket_for(self.key, user_id)
        # rollout is a percentage; buckets are 0..BUCKETS-1.
        threshold = self.rollout * (BUCKETS // 100)
        if bucket < threshold:
            return self._decision(user_id, True, REASON_ROLLOUT, bucket)
        return self._decision(user_id, False, REASON_OUTSIDE_ROLLOUT, bucket)

    def _decision(self, user_id: str, enabled: bool, reason: str, bucket: int) -> dict:
        return {
            "key": self.key,
            "user_id": user_id,
            "enabled": enabled,
            "reason": reason,
            "bucket": bucket,
            "rollout": self.rollout,
        }


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS flags (
    key         TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 0,
    rollout     INTEGER NOT NULL DEFAULT 0,
    allowlist   TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


class FlagStore:
    """SQLite-backed collection of feature flags."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FlagStore":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # -- reads -------------------------------------------------------------
    @staticmethod
    def _row_to_flag(row: sqlite3.Row) -> Flag:
        return Flag(
            key=row["key"],
            description=row["description"],
            enabled=bool(row["enabled"]),
            rollout=int(row["rollout"]),
            allowlist=tuple(json.loads(row["allowlist"] or "[]")),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def get_flag(self, key: str) -> Optional[Flag]:
        row = self._conn.execute(
            "SELECT * FROM flags WHERE key = ?", (normalise_key(key),)
        ).fetchone()
        return self._row_to_flag(row) if row else None

    def list_flags(self) -> list[Flag]:
        rows = self._conn.execute("SELECT * FROM flags ORDER BY key").fetchall()
        return [self._row_to_flag(row) for row in rows]

    def count_flags(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) FROM flags").fetchone()[0])

    # -- writes ------------------------------------------------------------
    def put_flag(self, data: dict) -> tuple[Flag, bool]:
        """Create or replace a flag. Returns ``(flag, created)``.

        A missing field on an existing flag keeps its stored value, so the same
        endpoint works for "create with defaults" and "update what I sent".
        """
        if not isinstance(data, dict):
            raise ValidationError("flag must be a JSON object")
        key = normalise_key(data.get("key"))
        existing = self.get_flag(key)

        description = (
            normalise_description(data["description"])
            if "description" in data
            else (existing.description if existing else "")
        )
        enabled = (
            normalise_enabled(data["enabled"])
            if "enabled" in data
            else (existing.enabled if existing else False)
        )
        rollout = (
            normalise_rollout(data["rollout"])
            if "rollout" in data
            else (existing.rollout if existing else 0)
        )
        allowlist = (
            normalise_allowlist(data["allowlist"])
            if "allowlist" in data
            else (existing.allowlist if existing else ())
        )

        now = _now()
        created_at = existing.created_at if existing else now
        self._conn.execute(
            """
            INSERT INTO flags (key, description, enabled, rollout, allowlist, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                description = excluded.description,
                enabled     = excluded.enabled,
                rollout     = excluded.rollout,
                allowlist   = excluded.allowlist,
                updated_at  = excluded.updated_at
            """,
            (
                key,
                description,
                int(enabled),
                rollout,
                json.dumps(list(allowlist)),
                created_at,
                now,
            ),
        )
        self._conn.commit()
        return self.get_flag(key), existing is None  # type: ignore[return-value]

    def update_flag(self, key: str, changes: dict) -> Flag:
        """Patch an existing flag; raises ``KeyError`` when it does not exist."""
        if not isinstance(changes, dict):
            raise ValidationError("flag must be a JSON object")
        existing = self.get_flag(key)
        if existing is None:
            raise KeyError(normalise_key(key))

        payload = {field_name: changes[field_name] for field_name in
                   ("description", "enabled", "rollout", "allowlist")
                   if field_name in changes}
        if not payload:
            raise ValidationError("no supported fields to update")
        payload["key"] = existing.key
        flag, _created = self.put_flag(payload)
        return flag

    def delete_flag(self, key: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM flags WHERE key = ?", (normalise_key(key),)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    # -- evaluation --------------------------------------------------------
    def evaluate(self, key: str, user_id: str) -> dict:
        """Evaluate one flag for one user. Raises ``KeyError`` if unknown."""
        flag = self.get_flag(key)
        if flag is None:
            raise KeyError(normalise_key(key))
        return flag.evaluate(normalise_user_id(user_id))

    def evaluate_all(self, user_id: str) -> list[dict]:
        """Evaluate every flag for one user, ordered by key."""
        user_id = normalise_user_id(user_id)
        return [flag.evaluate(user_id) for flag in self.list_flags()]
