"""Business-hours aware scheduling: working windows, holidays, and SLA deadlines.

A calendar knows which weekdays it is open, which hours of those days count,
and which dates are holidays. Everything else is arithmetic on top of that:

    from business_hours import BusinessCalendar

    cal = BusinessCalendar(("09:30", "18:30"), holidays=["2026-11-26"])
    cal.add_business_hours(datetime(2026, 11, 25, 17, 0), 4)
    # -> datetime(2026, 11, 27, 12, 30)   (holiday skipped, 2h on the 25th)

The module is pure standard library and has no dependencies outside `zoneinfo`.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, tzinfo
from typing import Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

__all__ = [
    "BusinessCalendar",
    "NoOpenWindowError",
]

#: How far to walk before giving up. ~10 years of days; a calendar with no
#: windows at all would otherwise loop forever.
MAX_DAYS = 3660

TimeLike = "dtime | str"


class NoOpenWindowError(ValueError):
    """Raised when a calendar has no working window in the search range."""


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------


def _as_time(value) -> dtime:
    """Accept ``time``, ``"09:30"``, ``"9:30"`` or ``"09:30:00"``."""
    if isinstance(value, dtime):
        if value.tzinfo is not None:
            raise ValueError(f"window times must be naive, got {value!r}")
        return value
    if isinstance(value, str):
        raw = value.strip()
        parts = raw.split(":")
        if len(parts) not in (2, 3):
            raise ValueError(f"cannot read time {value!r}; expected HH:MM or HH:MM:SS")
        try:
            hour, minute = int(parts[0]), int(parts[1])
            second = int(parts[2]) if len(parts) == 3 else 0
        except ValueError:
            raise ValueError(f"cannot read time {value!r}; expected HH:MM or HH:MM:SS") from None
        try:
            return dtime(hour, minute, second)
        except ValueError as exc:
            raise ValueError(f"invalid time {value!r}: {exc}") from None
    raise TypeError(f"expected a time or 'HH:MM' string, got {type(value).__name__}")


def _as_date(value) -> date:
    """Accept ``date``, ``datetime`` or an ISO ``"YYYY-MM-DD"`` string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            raise ValueError(f"cannot read date {value!r}; expected YYYY-MM-DD") from None
    raise TypeError(f"expected a date or 'YYYY-MM-DD' string, got {type(value).__name__}")


def _as_zone(value) -> tzinfo | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return ZoneInfo(value)
        except Exception:
            raise ValueError(f"unknown timezone {value!r}") from None
    if isinstance(value, tzinfo):
        return value
    raise TypeError(f"expected a timezone name or tzinfo, got {type(value).__name__}")


def _as_duration(value) -> timedelta:
    if isinstance(value, timedelta):
        duration = value
    elif isinstance(value, bool):
        raise TypeError("business hours must be a number, not a bool")
    elif isinstance(value, (int, float)):
        duration = timedelta(hours=value)
    else:
        raise TypeError(f"expected hours as int, float or timedelta, got {type(value).__name__}")
    return duration


# --------------------------------------------------------------------------
# calendar
# --------------------------------------------------------------------------


class BusinessCalendar:
    """Weekly working windows plus holidays.

    Parameters
    ----------
    hours:
        ``(open, close)`` for every working day, as ``time`` objects or
        ``"HH:MM"`` strings. A single window per day.
    workdays:
        Weekday numbers that are open, Monday is ``0`` (``datetime`` convention).
    holidays:
        Dates that are closed even if they fall on a working weekday.
    tz:
        Timezone name or ``tzinfo``. When set, every datetime you pass in is
        read as wall-clock time in that zone, and results come back in it.
        When ``None``, datetimes must be naive.
    """

    def __init__(
        self,
        hours: Sequence = ("09:00", "17:00"),
        *,
        workdays: Iterable[int] = (0, 1, 2, 3, 4),
        holidays: Iterable = (),
        tz=None,
    ) -> None:
        start, end = _parse_window(hours, where="hours")
        days = _validate_workdays(workdays)
        self._windows = {day: ((start, end),) for day in days}
        self._finish(holidays=holidays, tz=tz)

    @classmethod
    def from_windows(
        cls,
        windows: Mapping[int, Sequence],
        *,
        holidays: Iterable = (),
        tz=None,
    ) -> "BusinessCalendar":
        """Build a calendar with different (or multiple) windows per weekday.

        ``{0: [("09:00", "13:00"), ("14:00", "18:00")]}`` is a Monday split by
        a lunch break. A weekday left out of the mapping is closed.
        """
        if not isinstance(windows, Mapping):
            raise TypeError("windows must be a mapping of weekday -> sequence of windows")
        parsed: dict[int, tuple] = {}
        for day, spans in windows.items():
            if not isinstance(day, int) or not 0 <= day <= 6:
                raise ValueError(f"weekday must be an int from 0 to 6, got {day!r}")
            if isinstance(spans, (str, bytes)) or not isinstance(spans, Sequence):
                raise TypeError(f"windows for weekday {day} must be a sequence of (open, close)")
            day_spans = []
            for span in spans:
                day_spans.append(_parse_window(span, where=f"weekday {day}"))
            if not day_spans:
                continue
            parsed[day] = _merge_windows(day_spans, where=f"weekday {day}")
        if not parsed:
            raise ValueError("windows must open at least one weekday")

        calendar = cls.__new__(cls)
        calendar._windows = {day: tuple(spans) for day, spans in parsed.items()}
        calendar._finish(holidays=holidays, tz=tz)
        return calendar

    # -- construction helpers ------------------------------------------------

    def _finish(self, *, holidays: Iterable, tz) -> None:
        self._holidays = frozenset(_as_date(item) for item in holidays)
        self._tz = _as_zone(tz)
        self._name = None

    def __repr__(self) -> str:
        days = ",".join(str(day) for day in sorted(self._windows))
        zone = f", tz={self._tz.key!r}" if isinstance(self._tz, ZoneInfo) else ""
        return (
            f"BusinessCalendar(workdays=[{days}], "
            f"holidays={len(self._holidays)}{zone})"
        )

    # -- inspection ----------------------------------------------------------

    @property
    def holidays(self) -> frozenset:
        """The closed dates, as ``date`` objects."""
        return self._holidays

    @property
    def tz(self):
        return self._tz

    def is_workday(self, day) -> bool:
        """True when the date has working windows and is not a holiday."""
        day = _as_date(day)
        return day.weekday() in self._windows and day not in self._holidays

    def segments(self, day) -> tuple:
        """The open intervals on a date, as ``(start, end)`` datetimes."""
        day = _as_date(day)
        if day in self._holidays:
            return ()
        out = []
        for start, end in self._windows.get(day.weekday(), ()):
            out.append(
                (
                    self._stamp(datetime.combine(day, start)),
                    self._stamp(datetime.combine(day, end)),
                )
            )
        return tuple(out)

    def is_open(self, moment: datetime) -> bool:
        """True when the moment falls inside an open interval (end is exclusive)."""
        moment = self._localize(moment)
        return any(start <= moment < end for start, end in self.segments(moment.date()))

    def next_open(self, moment: datetime, *, inclusive: bool = True) -> datetime:
        """The first open moment at or after ``moment``.

        With ``inclusive=False`` a moment that is already open is skipped and
        the next opening (after a close) is returned instead.
        """
        cursor = self._localize(moment)
        for day in self._days_forward(cursor.date()):
            for start, end in self.segments(day):
                if end <= cursor:
                    continue
                if inclusive:
                    return max(start, cursor)
                if start > cursor:
                    return start
                if end > cursor:
                    continue
        raise NoOpenWindowError("no open window found after the given moment")

    # -- arithmetic ----------------------------------------------------------

    def add_business_hours(self, start: datetime, hours) -> datetime:
        """Advance ``start`` by ``hours`` of open time.

        ``hours`` may be an int, a float, or a ``timedelta``. A negative value
        walks backwards. Time spent closed is skipped, never counted.
        """
        duration = _as_duration(hours)
        if duration == timedelta(0):
            return self._localize(start)
        if duration < timedelta(0):
            return self._subtract(self._localize(start), -duration)
        return self._add(self._localize(start), duration)

    def business_time_between(self, start: datetime, end: datetime) -> timedelta:
        """Open time inside the closed interval ``[start, end]``."""
        first = self._localize(start)
        last = self._localize(end)
        if last < first:
            raise ValueError("end must not be before start")
        total = timedelta(0)
        day = first.date()
        for _ in range(MAX_DAYS + 1):
            if day > last.date():
                break
            for seg_start, seg_end in self.segments(day):
                low = max(seg_start, first)
                high = min(seg_end, last)
                if high > low:
                    total += high - low
            day += timedelta(days=1)
        return total

    # -- internals -----------------------------------------------------------

    def _add(self, cursor: datetime, remaining: timedelta) -> datetime:
        for day in self._days_forward(cursor.date()):
            for start, end in self.segments(day):
                point = max(cursor, start)
                if point >= end:
                    continue
                available = end - point
                if available >= remaining:
                    return point + remaining
                remaining -= available
                cursor = end
        raise NoOpenWindowError("no open window found within the search range")

    def _subtract(self, cursor: datetime, remaining: timedelta) -> datetime:
        day = cursor.date()
        for _ in range(MAX_DAYS + 1):
            for start, end in reversed(self.segments(day)):
                point = min(cursor, end)
                if point <= start:
                    continue
                available = point - start
                if available >= remaining:
                    return point - remaining
                remaining -= available
                cursor = start
            day -= timedelta(days=1)
            # Walk the previous day from its last instant, so every window on
            # it is fully available.
            cursor = self._stamp(datetime.combine(day, dtime.max))
        raise NoOpenWindowError("no open window found within the search range")

    def _days_forward(self, day: date):
        for offset in range(MAX_DAYS + 1):
            yield day + timedelta(days=offset)

    def _stamp(self, value: datetime) -> datetime:
        return value.replace(tzinfo=self._tz) if self._tz is not None else value

    def _localize(self, moment: datetime) -> datetime:
        if not isinstance(moment, datetime):
            raise TypeError(f"expected a datetime, got {type(moment).__name__}")
        if self._tz is None:
            if moment.tzinfo is not None:
                raise ValueError(
                    "this calendar has no timezone; pass a naive datetime "
                    "or build it with tz=..."
                )
            return moment
        if moment.tzinfo is None:
            return moment.replace(tzinfo=self._tz)
        return moment.astimezone(self._tz)


# --------------------------------------------------------------------------
# window validation
# --------------------------------------------------------------------------


def _parse_window(window, *, where: str) -> tuple:
    if isinstance(window, (str, bytes)) or not isinstance(window, Sequence) or len(window) != 2:
        raise ValueError(f"{where}: expected an (open, close) pair, got {window!r}")
    start = _as_time(window[0])
    end = _as_time(window[1])
    if end <= start:
        raise ValueError(
            f"{where}: close ({end.strftime('%H:%M')}) must be after open "
            f"({start.strftime('%H:%M')}); overnight windows are not supported"
        )
    return start, end


def _validate_workdays(workdays) -> tuple:
    if isinstance(workdays, (str, bytes)) or not isinstance(workdays, Iterable):
        raise TypeError("workdays must be an iterable of weekday numbers")
    days = []
    for day in workdays:
        if isinstance(day, bool) or not isinstance(day, int):
            raise TypeError(f"weekday must be an int from 0 to 6, got {day!r}")
        if not 0 <= day <= 6:
            raise ValueError(f"weekday must be an int from 0 to 6, got {day!r}")
        days.append(day)
    if not days:
        raise ValueError("workdays must contain at least one weekday")
    return tuple(sorted(set(days)))


def _merge_windows(spans: Sequence[tuple], *, where: str) -> tuple:
    """Sort windows and merge any that touch or overlap."""
    ordered = sorted(spans)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)
