"""Meeting-slot search core.

Pure, dependency-free logic: turn a list of busy calendar blocks into the free
slots that fit a meeting of a given length, inside working hours, in a chosen
time zone.

Everything is computed in UTC internally. Local times exist only at the edges
of the pipeline — when working hours are turned into an instant, and when a
slot is formatted for display — so daylight-saving changes are handled by the
``zoneinfo`` database rather than by arithmetic on wall-clock times.

Input is deliberately text-first: busy blocks arrive as ISO-8601 pairs
(``2026-11-16T09:00+05:30/2026-11-16T10:00+05:30``) so the same parser serves
the API, the CLI and the tests.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Sequence

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "InputError",
    "WEEKDAY_NAMES",
    "MAX_RANGE_DAYS",
    "parse_instant",
    "parse_busy",
    "merge_intervals",
    "subtract_intervals",
    "normalise_weekdays",
    "find_slots",
]

UTC = timezone.utc

WEEKDAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

#: Longest search window accepted, so a typo in a year cannot spin the server.
MAX_RANGE_DAYS = 62


class InputError(ValueError):
    """Raised for anything the caller can fix by correcting their input."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_instant(text: str, *, where: str = "instant") -> datetime:
    """Parse an ISO-8601 date-time that carries an explicit UTC offset.

    Naive strings are rejected: an offset is the only thing that makes a busy
    block unambiguous across time zones, and guessing one is how calendars
    silently disagree.
    """
    if not isinstance(text, str):
        raise InputError(f"{where}: expected a string, got {type(text).__name__}")
    raw = text.strip()
    if not raw:
        raise InputError(f"{where}: empty date-time")
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        raise InputError(
            f"{where}: {text!r} is not an ISO-8601 date-time "
            "(expected e.g. 2026-11-16T09:00+05:30 or 2026-11-16T03:30Z)"
        ) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise InputError(
            f"{where}: {text!r} has no UTC offset — add one "
            "(e.g. +05:30 for IST, or Z for UTC)"
        )
    return parsed.astimezone(UTC)


def parse_busy(text: str) -> list[tuple[datetime, datetime]]:
    """Parse a block of ``start/end`` lines into merged UTC intervals.

    Blank lines and ``#`` comments are ignored. Overlapping or touching blocks
    are merged, so callers never see the same minute twice.
    """
    if not isinstance(text, str):
        raise InputError("busy: expected a string of 'start/end' lines")
    intervals: list[tuple[datetime, datetime]] = []
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if "/" not in line:
            raise InputError(
                f"busy line {lineno}: expected 'start/end', got {raw_line.strip()!r}"
            )
        start_text, end_text = line.split("/", 1)
        start = parse_instant(start_text, where=f"busy line {lineno} start")
        end = parse_instant(end_text, where=f"busy line {lineno} end")
        if end <= start:
            raise InputError(f"busy line {lineno}: end must be after start")
        intervals.append((start, end))
    return merge_intervals(intervals)


def _parse_clock(text: str, *, where: str) -> time:
    if not isinstance(text, str) or not text.strip():
        raise InputError(f"{where}: expected a clock time like '09:00'")
    raw = text.strip()
    try:
        parsed = time.fromisoformat(raw)
    except ValueError:
        raise InputError(f"{where}: {text!r} is not a time like '09:00' or '18:30'") from None
    if parsed.tzinfo is not None:
        raise InputError(f"{where}: give a wall-clock time without an offset, e.g. '09:00'")
    return parsed


def _parse_date(text: str, *, where: str) -> date:
    if isinstance(text, date) and not isinstance(text, datetime):
        return text
    if not isinstance(text, str) or not text.strip():
        raise InputError(f"{where}: expected a date like '2026-11-16'")
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        raise InputError(f"{where}: {text!r} is not a date like '2026-11-16'") from None


def normalise_weekdays(value) -> tuple[int, ...]:
    """Normalise a weekday selection to a sorted tuple of ints (Mon = 0).

    Accepts ``None`` (Mon–Fri), ints ``0..6``, or names such as ``"mon"``,
    ``"Monday"`` and ``"wed"``, mixed freely.
    """
    if value is None:
        return (0, 1, 2, 3, 4)
    if isinstance(value, (str, int)):
        value = [value]
    if not isinstance(value, Iterable):
        raise InputError("weekdays: expected a list such as [0, 1, 2] or ['mon', 'tue']")

    selected: set[int] = set()
    for item in value:
        if isinstance(item, bool):
            raise InputError("weekdays: booleans are not weekdays")
        if isinstance(item, int):
            if not 0 <= item <= 6:
                raise InputError(f"weekdays: {item} is outside 0 (Mon) to 6 (Sun)")
            selected.add(item)
            continue
        if isinstance(item, str):
            key = item.strip().lower()[:3]
            if key in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}:
                selected.add(["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(key))
                continue
            raise InputError(f"weekdays: {item!r} is not a weekday name")
        raise InputError(f"weekdays: cannot interpret {item!r}")
    if not selected:
        raise InputError("weekdays: pick at least one day")
    return tuple(sorted(selected))


def _resolve_zone(name: str) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip():
        raise InputError("timezone: expected an IANA name such as 'Asia/Kolkata'")
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError):
        raise InputError(
            f"timezone: {name!r} is not a known IANA time zone "
            "(for example 'Asia/Kolkata', 'Europe/London', 'UTC')"
        ) from None


# ---------------------------------------------------------------------------
# Interval arithmetic
# ---------------------------------------------------------------------------


def merge_intervals(
    intervals: Iterable[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Sort and coalesce intervals; touching intervals are joined."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def subtract_intervals(
    window: tuple[datetime, datetime],
    busy: Sequence[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Return the parts of ``window`` not covered by ``busy``.

    ``busy`` need not be merged or sorted; anything outside ``window`` is
    ignored.
    """
    start, end = window
    free: list[tuple[datetime, datetime]] = []
    cursor = start
    for busy_start, busy_end in merge_intervals(busy):
        if busy_end <= cursor:
            continue
        if busy_start >= end:
            break
        if busy_start > cursor:
            free.append((cursor, busy_start))
        cursor = max(cursor, busy_end)
        if cursor >= end:
            break
    if cursor < end:
        free.append((cursor, end))
    return free


# ---------------------------------------------------------------------------
# Slot search
# ---------------------------------------------------------------------------


def _localise(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """Attach ``zone`` to a wall-clock time on ``day``."""
    return datetime(day.year, day.month, day.day, clock.hour, clock.minute,
                    clock.second, tzinfo=zone)


def _iter_dates(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _ceil_div(value: timedelta, step: timedelta) -> int:
    """Smallest ``k`` with ``k * step >= value`` (``value`` must be >= 0)."""
    return -((-value) // step)


def find_slots(
    busy: Sequence[tuple[datetime, datetime]],
    *,
    timezone_name: str = "Asia/Kolkata",
    start_date,
    end_date,
    work_start: str = "09:00",
    work_end: str = "18:00",
    weekdays=None,
    duration_min: int = 30,
    step_min: int = 15,
    limit: int = 20,
    earliest: Optional[datetime] = None,
) -> dict:
    """Find the earliest free slots of ``duration_min`` inside working hours.

    The candidate grid is anchored at each day's working-hours start, so a
    09:00–18:00 day with a 15-minute step offers 09:00, 09:15, 09:30, …
    regardless of where the free blocks happen to begin.

    ``earliest`` (an aware datetime, or ``None``) drops slots that start
    before it — useful for "from now on" searches.
    """
    zone = _resolve_zone(timezone_name)
    first_day = _parse_date(start_date, where="start_date")
    last_day = _parse_date(end_date, where="end_date")
    if last_day < first_day:
        raise InputError("end_date must not be before start_date")
    span = (last_day - first_day).days + 1
    if span > MAX_RANGE_DAYS:
        raise InputError(
            f"search window is {span} days; the maximum is {MAX_RANGE_DAYS}"
        )

    start_clock = _parse_clock(work_start, where="work_start")
    end_clock = _parse_clock(work_end, where="work_end")
    if end_clock <= start_clock:
        raise InputError("work_end must be after work_start")

    allowed_days = normalise_weekdays(weekdays)

    for label, value, low, high in (
        ("duration_min", duration_min, 1, 24 * 60),
        ("step_min", step_min, 1, 240),
        ("limit", limit, 1, 500),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            raise InputError(f"{label}: expected an integer")
        if not low <= value <= high:
            raise InputError(f"{label}: must be between {low} and {high}")

    if earliest is not None:
        if earliest.tzinfo is None or earliest.utcoffset() is None:
            raise InputError("earliest: needs a UTC offset")
        earliest = earliest.astimezone(UTC)

    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=step_min)
    merged_busy = merge_intervals(busy)

    slots: list[dict] = []
    days: list[dict] = []
    truncated = False

    for day in _iter_dates(first_day, last_day):
        weekday = day.weekday()
        if weekday not in allowed_days:
            days.append(
                {
                    "date": day.isoformat(),
                    "weekday": WEEKDAY_NAMES[weekday],
                    "included": False,
                    "reason": "not a working day",
                    "free_minutes": 0,
                    "slots": 0,
                }
            )
            continue

        window_start = _localise(day, start_clock, zone)
        window_end = _localise(day, end_clock, zone)
        start_utc = window_start.astimezone(UTC)
        end_utc = window_end.astimezone(UTC)

        day_slots = 0
        free_minutes = 0
        if end_utc > start_utc:
            free = subtract_intervals((start_utc, end_utc), merged_busy)
            free_minutes = int(
                sum((end - begin).total_seconds() for begin, end in free) // 60
            )
            for free_start, free_end in free:
                offset = free_start - start_utc
                candidate = start_utc + _ceil_div(offset, step) * step
                while candidate + duration <= free_end:
                    if earliest is None or candidate >= earliest:
                        local_start = candidate.astimezone(zone)
                        local_end = (candidate + duration).astimezone(zone)
                        slots.append(
                            {
                                "date": day.isoformat(),
                                "weekday": WEEKDAY_NAMES[weekday],
                                "start": local_start.isoformat(),
                                "end": local_end.isoformat(),
                                "start_utc": candidate.isoformat().replace("+00:00", "Z"),
                                "end_utc": (candidate + duration)
                                .isoformat()
                                .replace("+00:00", "Z"),
                                "duration_min": duration_min,
                            }
                        )
                        day_slots += 1
                        if len(slots) >= limit:
                            truncated = True
                            break
                    candidate += step
                if truncated:
                    break

        days.append(
            {
                "date": day.isoformat(),
                "weekday": WEEKDAY_NAMES[weekday],
                "included": True,
                "reason": "",
                "free_minutes": free_minutes,
                "slots": day_slots,
            }
        )
        if truncated:
            break

    return {
        "timezone": str(zone),
        "window": {
            "start_date": first_day.isoformat(),
            "end_date": last_day.isoformat(),
            "work_start": work_start.strip(),
            "work_end": work_end.strip(),
            "weekdays": list(allowed_days),
            "duration_min": duration_min,
            "step_min": step_min,
            "limit": limit,
        },
        "slots": slots,
        "days": days,
        "truncated": truncated,
        "complete": not truncated and len(days) == span,
    }
