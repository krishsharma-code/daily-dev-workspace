"""cronschedule: parse five-field cron expressions and compute their fire times.

Standard library only, Python 3.9+.

    from cronschedule import CronExpression

    schedule = CronExpression("0 9-17 * * MON-FRI")
    schedule.next_after(datetime(2026, 10, 29, 21, 2))
    # datetime(2026, 10, 30, 9, 0)

The expression is compiled once into sets of allowed values, so answering
"when does this fire next?" never scans minute by minute through empty space:
the search jumps to the next allowed month, then the next matching day, then
the next allowed hour and minute inside that day.
"""

from __future__ import annotations

import bisect
import datetime as dt

__all__ = ["CronError", "CronExpression", "ScheduleExhausted"]

#: How far ahead to look before declaring that an expression never fires.
#: Eight years always contains a 29 February, so leap-day schedules resolve.
_HORIZON_DAYS = 366 * 8

_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_DAY_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}

_DAY_LABELS = (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
)

_MONTH_LABELS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


class CronError(ValueError):
    """Raised when a cron expression cannot be parsed."""


class ScheduleExhausted(CronError):
    """Raised when an expression has no fire time within the search horizon."""


def _lookup(token, names, lo, hi, field):
    """Resolve one field token to an integer, or raise a helpful CronError."""
    text = token.strip().lower()
    if text.isdigit():
        value = int(text)
    elif names and text in names:
        value = names[text]
    else:
        known = f" or one of {', '.join(sorted(names))}" if names else ""
        raise CronError(
            f"{field} field: {token!r} is not a number{known}"
        )
    if not lo <= value <= hi:
        raise CronError(f"{field} field: {value} is outside {lo}-{hi}")
    return value


def _parse_field(text, lo, hi, field, names=None, normalize=None):
    """Expand one comma-separated cron field into the frozenset it denotes."""
    values = set()
    for term in text.split(","):
        if not term:
            raise CronError(f"{field} field: empty term in {text!r}")

        base = term
        step = 1
        if "/" in term:
            base, _, step_text = term.partition("/")
            if not base:
                raise CronError(f"{field} field: missing value before '/' in {term!r}")
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(
                    f"{field} field: step {step_text!r} must be a positive integer"
                )
            step = int(step_text)

        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            left, _, right = base.partition("-")
            start = _lookup(left, names, lo, hi, field)
            end = _lookup(right, names, lo, hi, field)
            if start > end:
                raise CronError(f"{field} field: range {base!r} is reversed")
        else:
            start = _lookup(base, names, lo, hi, field)
            # "5/15" means "from 5, every 15, up to the field maximum".
            end = hi if step != 1 or "/" in term else start

        values.update(range(start, end + 1, step))

    if normalize is not None:
        values = {normalize(value) for value in values}
    return frozenset(values)


def _compress(values):
    """Render sorted integers as compact ranges: {1,2,3,5} -> '1-3,5'."""
    items = sorted(values)
    chunks = []
    index = 0
    while index < len(items):
        stop = index
        while stop + 1 < len(items) and items[stop + 1] == items[stop] + 1:
            stop += 1
        if stop - index >= 2:
            chunks.append(f"{items[index]}-{items[stop]}")
        else:
            chunks.extend(str(value) for value in items[index:stop + 1])
        index = stop + 1
    return ",".join(chunks)


def _first_at_least(values, floor):
    """Smallest member of a sorted tuple that is >= floor, or None."""
    position = bisect.bisect_left(values, floor)
    return values[position] if position < len(values) else None


def _next_month_start(moment):
    if moment.month == 12:
        return moment.replace(year=moment.year + 1, month=1, day=1, hour=0, minute=0)
    return moment.replace(month=moment.month + 1, day=1, hour=0, minute=0)


def _next_day_start(moment):
    return (moment + dt.timedelta(days=1)).replace(hour=0, minute=0)


class CronExpression:
    """A parsed five-field cron expression.

    Fields are ``minute hour day-of-month month day-of-week``. Each accepts
    ``*``, single values, ``a-b`` ranges, ``/step`` steps, comma-separated
    lists, and (for months and weekdays) three-letter names. Sunday is ``0``,
    with ``7`` accepted as an alias. ``@daily`` and friends are supported.

    Day-of-month and day-of-week follow classic Vixie cron: when *both* fields
    are restricted the schedule fires when *either* matches.
    """

    def __init__(self, expression):
        if not isinstance(expression, str):
            raise TypeError("cron expression must be a str")

        text = " ".join(expression.split())
        if not text:
            raise CronError("empty cron expression")

        if text.startswith("@"):
            macro = _MACROS.get(text.lower())
            if macro is None:
                known = ", ".join(sorted(_MACROS))
                raise CronError(f"unknown macro {text!r}; known macros: {known}")
            text = macro

        fields = text.split(" ")
        if len(fields) != 5:
            raise CronError(
                f"expected 5 fields (minute hour day-of-month month day-of-week), "
                f"got {len(fields)} in {expression!r}"
            )

        minute_text, hour_text, dom_text, month_text, dow_text = fields

        self.expression = " ".join(fields)
        self.minutes = _parse_field(minute_text, 0, 59, "minute")
        self.hours = _parse_field(hour_text, 0, 23, "hour")
        self.days_of_month = _parse_field(dom_text, 1, 31, "day-of-month")
        self.months = _parse_field(month_text, 1, 12, "month", names=_MONTH_NAMES)
        self.days_of_week = _parse_field(
            dow_text, 0, 7, "day-of-week",
            names=_DAY_NAMES, normalize=lambda value: value % 7,
        )

        self._dom_restricted = dom_text != "*"
        self._dow_restricted = dow_text != "*"
        self._hours = tuple(sorted(self.hours))
        self._minute_list = tuple(sorted(self.minutes))

    # -- representation ---------------------------------------------------

    def __str__(self):
        return self.expression

    def __repr__(self):
        return f"CronExpression({self.expression!r})"

    def __eq__(self, other):
        if not isinstance(other, CronExpression):
            return NotImplemented
        return (self.minutes, self.hours, self.days_of_month, self.months,
                self.days_of_week, self._dom_restricted,
                self._dow_restricted) == (
            other.minutes, other.hours, other.days_of_month, other.months,
            other.days_of_week, other._dom_restricted,
            other._dow_restricted)

    def __hash__(self):
        return hash((self.minutes, self.hours, self.days_of_month, self.months,
                     self.days_of_week, self._dom_restricted,
                     self._dow_restricted))

    # -- matching ---------------------------------------------------------

    def _day_matches(self, moment):
        dom_ok = moment.day in self.days_of_month
        # Python counts Monday as 0; cron counts Sunday as 0.
        dow_ok = (moment.weekday() + 1) % 7 in self.days_of_week

        if self._dom_restricted and self._dow_restricted:
            return dom_ok or dow_ok
        if self._dom_restricted:
            return dom_ok
        if self._dow_restricted:
            return dow_ok
        return True

    def matches(self, moment):
        """True if the expression fires at any point during ``moment``'s minute.

        Seconds and microseconds are ignored, so this answers "is 09:30:47
        inside a firing minute?" rather than "is 09:30:47 the exact fire time?".
        """
        if not isinstance(moment, dt.datetime):
            raise TypeError("matches() expects a datetime")
        return (
            moment.minute in self.minutes
            and moment.hour in self.hours
            and moment.month in self.months
            and self._day_matches(moment)
        )

    # -- scheduling -------------------------------------------------------

    def next_after(self, moment):
        """First fire time strictly after ``moment``.

        ``moment`` may be naive or timezone-aware; an aware value keeps its
        ``tzinfo`` and is advanced in wall-clock terms (no DST correction).
        """
        if not isinstance(moment, dt.datetime):
            raise TypeError("next_after() expects a datetime")

        current = (moment + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)
        limit = current + dt.timedelta(days=_HORIZON_DAYS)

        while current <= limit:
            if current.month not in self.months:
                current = _next_month_start(current)
                continue

            if not self._day_matches(current):
                current = _next_day_start(current)
                continue

            hour = _first_at_least(self._hours, current.hour)
            if hour is None:
                current = _next_day_start(current)
                continue
            if hour > current.hour:
                return current.replace(hour=hour, minute=self._minute_list[0])

            minute = _first_at_least(self._minute_list, current.minute)
            if minute is None:
                hour = _first_at_least(self._hours, current.hour + 1)
                if hour is None:
                    current = _next_day_start(current)
                    continue
                return current.replace(hour=hour, minute=self._minute_list[0])

            return current.replace(minute=minute)

        raise ScheduleExhausted(
            f"{self.expression!r} has no fire time within "
            f"{_HORIZON_DAYS} days of {moment.isoformat()}"
        )

    def iter_after(self, moment, count):
        """The next ``count`` fire times, as a tuple of datetimes."""
        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an int")
        if count < 0:
            raise ValueError("count must be >= 0")

        upcoming = []
        cursor = moment
        for _ in range(count):
            cursor = self.next_after(cursor)
            upcoming.append(cursor)
        return tuple(upcoming)

    # -- description ------------------------------------------------------

    def describe(self):
        """A short English rendering of the schedule."""
        parts = []

        every_minute = self.minutes == frozenset(range(60))
        every_hour = self.hours == frozenset(range(24))

        if every_minute and every_hour:
            parts.append("every minute")
        else:
            if every_minute:
                parts.append("every minute")
            elif len(self.minutes) == 1:
                parts.append(f"at minute {next(iter(self.minutes))}")
            else:
                parts.append(f"at minutes {_compress(self.minutes)}")

            if not every_hour:
                label = "hour" if len(self.hours) == 1 else "hours"
                parts.append(f"past {label} {_compress(self.hours)}")

        day_parts = []
        if self._dom_restricted:
            day_parts.append(f"on day(s) {_compress(self.days_of_month)} of the month")
        if self._dow_restricted:
            names = [_DAY_LABELS[value] for value in sorted(self.days_of_week)]
            day_parts.append("on " + ", ".join(names))
        if day_parts:
            # Both fields restricted means either one matches, so say "or".
            parts.append(" or ".join(day_parts))

        if self.months != frozenset(range(1, 13)):
            names = [_MONTH_LABELS[value - 1] for value in sorted(self.months)]
            parts.append("in " + ", ".join(names))

        if len(parts) == 1:
            return parts[0]
        return ", ".join(parts[:-1]) + ", " + parts[-1]
