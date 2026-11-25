"""Streak arithmetic for habits that are not necessarily daily.

Everything here is a pure function over plain dates: no database, no clock, no
sockets. That keeps the interesting part -- what counts as an unbroken run --
cheap to test and easy to reason about.

Vocabulary
----------
``weekdays``
    A tuple of ISO weekday numbers, Monday ``0`` .. Sunday ``6``. A habit is
    *scheduled* on those days and only those. The default is all seven days.

``day``
    A :class:`datetime.date`. Days are compared, never timestamps, so a check-in
    at 23:59 and one at 00:01 are different days but the same kind of thing.

The one rule that surprises people
----------------------------------
A habit scheduled today that has not been checked in yet does **not** break the
streak: the day is not over. A scheduled day in the *past* that was missed does
break it, immediately. That distinction is why :func:`current_streak` needs
``today`` while :func:`longest_streak` does not.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

__all__ = [
    "ALL_DAYS",
    "StreakError",
    "completion_rate",
    "current_streak",
    "format_day",
    "is_scheduled",
    "longest_streak",
    "next_scheduled_day",
    "normalise_weekdays",
    "parse_day",
    "previous_scheduled_day",
    "scheduled_days",
    "streak_summary",
    "weekday_label",
]

ALL_DAYS: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)

WEEKDAY_NAMES: tuple[str, ...] = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WEEKDAY_ALIASES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "weds": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_ALL_WORDS = {"all", "daily", "everyday", "every-day", "every day"}


class StreakError(ValueError):
    """Raised when a day, weekday set or date range is not usable."""


# ---------------------------------------------------------------------------
# Days and weekdays
# ---------------------------------------------------------------------------


def parse_day(value: object) -> date:
    """Parse a strict ``YYYY-MM-DD`` string into a :class:`date`.

    Deliberately strict: ``2026-1-5``, ``2026-01-05T10:00`` and
    ``05-01-2026`` are all rejected rather than guessed at, because a habit log
    that silently records the wrong day is worse than one that asks again.
    """
    if isinstance(value, date) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise StreakError("day must be a YYYY-MM-DD string")
    text = value.strip()
    if not _DAY_RE.match(text):
        raise StreakError(f"day must look like YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise StreakError(f"{value!r} is not a real date") from exc


def format_day(day: date) -> str:
    """Render a date the way the API and the database store it."""
    return day.isoformat()


def weekday_label(index: int) -> str:
    """``0`` -> ``"Mon"``; used for labels in the client."""
    if not 0 <= index <= 6:
        raise StreakError("weekday index must be between 0 and 6")
    return WEEKDAY_NAMES[index][:3].title()


def normalise_weekdays(value: object) -> tuple[int, ...]:
    """Turn a loose weekday description into a sorted, de-duplicated tuple.

    Accepts ``None`` (every day), a single int, a comma-separated string
    (``"mon,wed,fri"``), a list of ints and/or names, or the word ``"daily"``.
    """
    if value is None:
        return ALL_DAYS
    if isinstance(value, bool):
        raise StreakError("weekdays must not be a boolean")
    if isinstance(value, int):
        items: Sequence[object] = (value,)
    elif isinstance(value, str):
        text = value.strip().lower()
        if not text:
            raise StreakError("weekdays must not be empty")
        if text in _ALL_WORDS:
            return ALL_DAYS
        items = [part for part in re.split(r"[,\s]+", text) if part]
    elif isinstance(value, Iterable):
        items = list(value)
    else:
        raise StreakError("weekdays must be a list, a string or an int")

    if not items:
        raise StreakError("a habit needs at least one scheduled weekday")

    picked: set[int] = set()
    for item in items:
        if isinstance(item, bool):
            raise StreakError("weekdays must not contain booleans")
        if isinstance(item, int):
            if not 0 <= item <= 6:
                raise StreakError("weekday numbers must be between 0 and 6")
            picked.add(item)
        elif isinstance(item, str):
            key = item.strip().lower()
            if key in _ALL_WORDS:
                picked.update(ALL_DAYS)
            elif key in _WEEKDAY_ALIASES:
                picked.add(_WEEKDAY_ALIASES[key])
            else:
                raise StreakError(f"unknown weekday {item!r}")
        else:
            raise StreakError("weekdays must be numbers or names")
    if not picked:
        raise StreakError("a habit needs at least one scheduled weekday")
    return tuple(sorted(picked))


def is_scheduled(day: date, weekdays: object = None) -> bool:
    """Is a habit with these weekdays due on ``day``?"""
    return day.weekday() in normalise_weekdays(weekdays)


def previous_scheduled_day(day: date, weekdays: object = None) -> Optional[date]:
    """The latest scheduled day strictly before ``day``."""
    days = normalise_weekdays(weekdays)
    cursor = day
    for _ in range(7):
        cursor -= timedelta(days=1)
        if cursor.weekday() in days:
            return cursor
    return None  # pragma: no cover - unreachable while ``days`` is non-empty


def next_scheduled_day(day: date, weekdays: object = None) -> Optional[date]:
    """The earliest scheduled day strictly after ``day``."""
    days = normalise_weekdays(weekdays)
    cursor = day
    for _ in range(7):
        cursor += timedelta(days=1)
        if cursor.weekday() in days:
            return cursor
    return None  # pragma: no cover - unreachable while ``days`` is non-empty


def scheduled_days(
    start: date, end: date, weekdays: object = None
) -> list[date]:
    """Every scheduled day in the closed range ``start`` .. ``end``."""
    if start > end:
        raise StreakError("start must not be after end")
    days = normalise_weekdays(weekdays)
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() in days:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _as_day_set(days: Iterable[object]) -> set[date]:
    return {parse_day(day) for day in days}


# ---------------------------------------------------------------------------
# Streaks
# ---------------------------------------------------------------------------


def current_streak(
    days: Iterable[object], today: object, weekdays: object = None
) -> int:
    """How many scheduled days in a row are done, counting back from today.

    * A checked-in scheduled day extends the streak.
    * Today, if scheduled and not yet done, is skipped rather than counted as a
      miss -- the day is still in progress.
    * Any earlier scheduled day without a check-in ends the run.
    """
    today = parse_day(today)
    done = {day for day in _as_day_set(days) if day <= today}
    days_of_week = normalise_weekdays(weekdays)

    if today.weekday() in days_of_week and today in done:
        cursor: Optional[date] = today
    else:
        cursor = previous_scheduled_day(today, days_of_week)

    streak = 0
    while cursor is not None and cursor in done:
        streak += 1
        cursor = previous_scheduled_day(cursor, days_of_week)
    return streak


def longest_streak(days: Iterable[object], weekdays: object = None) -> int:
    """The longest unbroken run of scheduled days ever recorded."""
    days_of_week = normalise_weekdays(weekdays)
    ordered = sorted(_as_day_set(days))
    if not ordered:
        return 0
    best = run = 1
    for previous, current in zip(ordered, ordered[1:]):
        if next_scheduled_day(previous, days_of_week) == current:
            run += 1
        else:
            run = 1
        best = max(best, run)
    return best


def completion_rate(
    days: Iterable[object],
    start: object,
    end: object,
    weekdays: object = None,
) -> dict:
    """Done vs scheduled inside a closed date range.

    Returns ``{"done", "scheduled", "missed", "percent"}``; ``percent`` is
    rounded to one decimal so it reads well in a UI without pretending to be
    more precise than a count of days.
    """
    start_day, end_day = parse_day(start), parse_day(end)
    due = scheduled_days(start_day, end_day, weekdays)
    done = _as_day_set(days) & set(due)
    total = len(due)
    return {
        "done": len(done),
        "scheduled": total,
        "missed": [format_day(day) for day in due if day not in done],
        "percent": round(100.0 * len(done) / total, 1) if total else 0.0,
    }


def streak_summary(
    days: Iterable[object],
    today: object,
    weekdays: object = None,
    window_days: int = 30,
) -> dict:
    """The numbers a habit card shows, computed in one pass.

    ``window_days`` is the length of the completion window that ends today
    (inclusive), so ``30`` means "the last 30 calendar days".
    """
    today = parse_day(today)
    if isinstance(window_days, bool) or not isinstance(window_days, int):
        raise StreakError("window_days must be an integer")
    if not 1 <= window_days <= 3660:
        raise StreakError("window_days must be between 1 and 3660")

    checked = _as_day_set(days)
    window_start = today - timedelta(days=window_days - 1)
    rate = completion_rate(checked, window_start, today, weekdays)
    return {
        "current": current_streak(checked, today, weekdays),
        "longest": longest_streak(checked, weekdays),
        "total_checkins": len([day for day in checked if day <= today]),
        "done_today": today in checked,
        "scheduled_today": is_scheduled(today, weekdays),
        "window": {
            "start": format_day(window_start),
            "end": format_day(today),
            "days": window_days,
            **rate,
        },
    }
