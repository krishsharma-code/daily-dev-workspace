"""Indian fiscal year (April–March) and quarter arithmetic.

India's financial year runs from 1 April to 31 March, so ``date.year`` is the
wrong answer for any date in January, February or March. An invoice dated
15 May 2025 belongs to FY 2025-26; one dated 20 February 2026 belongs to the
*same* fiscal year, not to FY 2026-27. GST returns, TDS quarters, payroll
statements and every annual report key off that boundary.

This module is the dependency-free answer to the five questions that come up
whenever dates meet Indian reporting:

    from fiscal_year import fy_of, fy_label, quarter_of, parse_fy

    fy_of(date(2026, 2, 20))        # 2025
    fy_label(2025)                  # 'FY2025-26'
    quarter_of(date(2025, 8, 1))    # 2
    parse_fy('FY 25-26')            # 2025
    fy_bounds(2025)                 # (date(2025, 4, 1), date(2026, 3, 31))

Design notes
------------
* A fiscal year is identified by the calendar year in which it **starts**.
  FY 2025-26 starts on 1 April 2025 and ends on 31 March 2026, so ``fy_of``
  returns ``2025`` for both 1 April 2025 and 31 March 2026.
* Quarters are fiscal, not calendar: Q1 is Apr–Jun, Q2 Jul–Sep, Q3 Oct–Dec,
  Q4 Jan–Mar. ``quarter_of(date(2026, 1, 5))`` is ``4``, not ``1``.
* ``datetime`` is accepted anywhere a date is (it is a ``date`` subclass) and
  is truncated to its calendar day. Anything else raises ``FiscalYearError``
  instead of guessing.
* ``parse_fy`` reads the labels people actually type — ``FY2025-26``,
  ``2025-2026``, ``2025/26``, ``FY 25``, even a bare ``2025`` — and raises on
  anything ambiguous or inconsistent such as ``2025-2027`` or ``26-25``.
* All arithmetic is on ``datetime.date`` with no timezone or locale
  assumptions, so results are identical on every machine.
"""

from __future__ import annotations

import datetime as _dt
import re
from datetime import date  # noqa: F401  (docstring examples use the plain name)

__all__ = [
    "FiscalYearError",
    "MIN_YEAR",
    "MAX_YEAR",
    "fy_of",
    "fy_bounds",
    "quarter_of",
    "quarter_bounds",
    "month_of_fy",
    "fy_label",
    "quarter_label",
    "parse_fy",
    "shift_fy",
    "fy_months",
    "fy_quarters",
    "fy_days",
    "fy_span",
]

#: Smallest fiscal-year start year the module will produce. The lower bound
#: keeps four-digit labels meaningful; the upper bound leaves room for the
#: 31 March that closes the year (9998-04-01 .. 9999-03-31 is representable).
MIN_YEAR = 1000
MAX_YEAR = 9998

_FY_START_MONTH = 4
_FY_START_DAY = 1

#: Fiscal quarter -> calendar month the quarter opens in.
_QUARTER_OPEN_MONTH = {1: 4, 2: 7, 3: 10, 4: 1}

_LABEL_STYLES = ("short", "long", "plain", "compact")

_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"

_FY_RE = re.compile(r"^(?:FY)?(\d{4})(?:[-/](\d{4}|\d{2}))?$")
_FY_SHORT_RE = re.compile(r"^(?:FY)?(\d{2})(?:[-/](\d{2}))?$")


class FiscalYearError(ValueError):
    """Raised for anything that is not a date, a valid year, or a FY label."""


def _as_date(value, name="value"):
    """Coerce a date or datetime to a plain ``datetime.date``."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    raise FiscalYearError(
        f"{name} must be a date or datetime, got {type(value).__name__}"
    )


def _check_year(year):
    """Validate a fiscal-year start year and return it."""
    if isinstance(year, bool) or not isinstance(year, int):
        raise FiscalYearError(
            f"fiscal year must be an int, got {type(year).__name__}"
        )
    if not MIN_YEAR <= year <= MAX_YEAR:
        raise FiscalYearError(
            f"fiscal year must be between {MIN_YEAR} and {MAX_YEAR}, got {year}"
        )
    return year


def _check_quarter(quarter):
    """Validate a fiscal quarter number (1-4) and return it."""
    if isinstance(quarter, bool) or not isinstance(quarter, int):
        raise FiscalYearError(
            f"quarter must be an int, got {type(quarter).__name__}"
        )
    if not 1 <= quarter <= 4:
        raise FiscalYearError(f"quarter must be 1, 2, 3 or 4, got {quarter}")
    return quarter


def fy_of(value):
    """Return the start year of the fiscal year containing ``value``.

    >>> fy_of(date(2025, 4, 1))
    2025
    >>> fy_of(date(2026, 3, 31))
    2025
    """
    day = _as_date(value)
    return day.year if day.month >= _FY_START_MONTH else day.year - 1


def fy_bounds(year):
    """Return ``(first_day, last_day)`` of fiscal year ``year``, inclusive.

    >>> fy_bounds(2025)
    (datetime.date(2025, 4, 1), datetime.date(2026, 3, 31))
    """
    year = _check_year(year)
    return (
        _dt.date(year, _FY_START_MONTH, _FY_START_DAY),
        _dt.date(year + 1, 3, 31),
    )


def quarter_of(value):
    """Return the fiscal quarter (1-4) containing ``value``.

    Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar.

    >>> quarter_of(date(2025, 8, 1))
    2
    >>> quarter_of(date(2026, 1, 5))
    4
    """
    day = _as_date(value)
    # Months since 1 April: Apr -> 0, Mar -> 11.
    months_since_start = (day.month - _FY_START_MONTH) % 12
    return months_since_start // 3 + 1


def quarter_bounds(year, quarter):
    """Return ``(first_day, last_day)`` of a fiscal quarter, inclusive.

    >>> quarter_bounds(2025, 4)
    (datetime.date(2026, 1, 1), datetime.date(2026, 3, 31))
    """
    year = _check_year(year)
    quarter = _check_quarter(quarter)
    open_month = _QUARTER_OPEN_MONTH[quarter]
    open_year = year if open_month >= _FY_START_MONTH else year + 1
    start = _dt.date(open_year, open_month, 1)
    # The last day of a quarter is the day before the next one opens, which
    # keeps leap years and 31-day months correct without a table.
    close_month = open_month + 3
    if close_month > 12:
        end = _dt.date(open_year + 1, close_month - 12, 1) - _dt.timedelta(days=1)
    else:
        end = _dt.date(open_year, close_month, 1) - _dt.timedelta(days=1)
    return (start, end)


def month_of_fy(value):
    """Return the 1-based month index inside the fiscal year (Apr = 1, Mar = 12).

    >>> month_of_fy(date(2025, 4, 10))
    1
    >>> month_of_fy(date(2026, 3, 1))
    12
    """
    day = _as_date(value)
    return (day.month - _FY_START_MONTH) % 12 + 1


def fy_label(year, style="short"):
    """Format a fiscal year as a human label.

    ``style`` is one of ``short`` (``'FY2025-26'``), ``long``
    (``'FY2025-2026'``), ``plain`` (``'2025-26'``) or ``compact``
    (``'FY25'``).

    >>> fy_label(2025)
    'FY2025-26'
    >>> fy_label(2025, style='long')
    'FY2025-2026'
    >>> fy_label(2025, style='compact')
    'FY25'
    """
    year = _check_year(year)
    if style not in _LABEL_STYLES:
        raise FiscalYearError(
            f"style must be one of {', '.join(_LABEL_STYLES)}, got {style!r}"
        )
    end_year = year + 1
    if style == "short":
        return f"FY{year}-{end_year % 100:02d}"
    if style == "long":
        return f"FY{year}-{end_year}"
    if style == "plain":
        return f"{year}-{end_year % 100:02d}"
    return f"FY{year % 100:02d}"


def quarter_label(year, quarter, style="short"):
    """Format a fiscal quarter, e.g. ``'Q1 FY2025-26'``.

    >>> quarter_label(2025, 4)
    'Q4 FY2025-26'
    """
    year = _check_year(year)
    quarter = _check_quarter(quarter)
    return f"Q{quarter} {fy_label(year, style)}"


def parse_fy(text):
    """Read a fiscal-year label and return its start year.

    Accepts four-digit spans (``FY2025-26``, ``2025-2026``, ``2025/26``),
    two-digit forms (``FY25``, ``25-26``) and a bare start year (``2025``).
    Raises ``FiscalYearError`` on anything ambiguous, such as a span that is
    not exactly one year (``2025-2027``) or a reversed pair (``26-25``).

    A two-digit label carries only a century's worth of information, so it is
    read as 20xx: ``'FY99'`` is 2099, not 1999. Use a four-digit label for
    any year outside 2000-2099.

    >>> parse_fy('FY 25-26')
    2025
    >>> parse_fy(2025)
    2025
    """
    if isinstance(text, int) and not isinstance(text, bool):
        return _check_year(text)
    if not isinstance(text, str):
        raise FiscalYearError(
            f"expected a fiscal-year label, got {type(text).__name__}"
        )

    cleaned = text.strip()
    if not cleaned:
        raise FiscalYearError("fiscal-year label is empty")
    for dash in _DASHES:
        cleaned = cleaned.replace(dash, "-")
    cleaned = "".join(cleaned.split()).upper()

    match = _FY_RE.match(cleaned)
    if match:
        start = int(match.group(1))
        end_text = match.group(2)
        if end_text is not None:
            expected = (start + 1) % 100 if len(end_text) == 2 else start + 1
            if int(end_text) != expected:
                raise FiscalYearError(
                    f"{text!r} does not span exactly one fiscal year "
                    f"(expected {start}-{expected % 100:02d})"
                )
        return _check_year(start)

    match = _FY_SHORT_RE.match(cleaned)
    if match:
        start = 2000 + int(match.group(1))
        end_text = match.group(2)
        if end_text is not None and int(end_text) != (start + 1) % 100:
            raise FiscalYearError(
                f"{text!r} does not span exactly one fiscal year "
                f"(expected {start % 100:02d}-{(start + 1) % 100:02d})"
            )
        return _check_year(start)

    raise FiscalYearError(f"not a fiscal-year label: {text!r}")


def shift_fy(year, periods):
    """Move ``periods`` fiscal years forward (negative to go back).

    >>> shift_fy(2025, -1)
    2024
    """
    year = _check_year(year)
    if isinstance(periods, bool) or not isinstance(periods, int):
        raise FiscalYearError(
            f"periods must be an int, got {type(periods).__name__}"
        )
    return _check_year(year + periods)


def fy_months(year):
    """Return the 12 ``(calendar_year, month)`` pairs of a fiscal year, in order.

    >>> fy_months(2025)[0]
    (2025, 4)
    >>> fy_months(2025)[-1]
    (2026, 3)
    """
    year = _check_year(year)
    months = []
    for index in range(12):
        month = ((_FY_START_MONTH - 1 + index) % 12) + 1
        # April to December fall in the start year, January to March in the
        # year after it.
        months.append((year if index < 9 else year + 1, month))
    return tuple(months)


def fy_quarters(year):
    """Return the four fiscal quarters of ``year`` as ``(start, end)`` pairs.

    >>> len(fy_quarters(2025))
    4
    """
    year = _check_year(year)
    return tuple(quarter_bounds(year, quarter) for quarter in (1, 2, 3, 4))


def fy_days(year):
    """Return the number of days in fiscal year ``year`` (365 or 366).

    A leap day falls inside the fiscal year that contains the following
    February, so FY 2023-24 has 366 days.

    >>> fy_days(2023)
    366
    >>> fy_days(2025)
    365
    """
    start, end = fy_bounds(year)
    return (end - start).days + 1


def fy_span(start, end):
    """Return every fiscal-year start year touched by ``start``..``end``.

    Handy for reports that cross a 1 April boundary.

    >>> fy_span(date(2025, 12, 1), date(2026, 5, 1))
    (2025, 2026)
    """
    first = _as_date(start, "start")
    last = _as_date(end, "end")
    if last < first:
        raise FiscalYearError("start must not be after end")
    first_fy = fy_of(first)
    last_fy = fy_of(last)
    return tuple(range(first_fy, last_fy + 1))
