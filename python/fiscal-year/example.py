"""Runnable demo for fiscal_year: bucket a ledger by Indian fiscal year.

    python example.py
"""

import datetime as dt

from fiscal_year import (
    FiscalYearError,
    fy_bounds,
    fy_days,
    fy_label,
    fy_of,
    fy_quarters,
    fy_span,
    month_of_fy,
    parse_fy,
    quarter_label,
    quarter_of,
)

# (invoice date, amount in rupees) — deliberately straddles 1 April so the
# fiscal-year boundary is what decides the bucket, not the calendar year.
LEDGER = [
    (dt.date(2025, 4, 3), 11800),
    (dt.date(2025, 5, 21), 4500),
    (dt.date(2025, 8, 14), 23000),
    (dt.date(2025, 11, 2), 7600),
    (dt.date(2026, 1, 9), 15000),
    (dt.date(2026, 2, 27), 3250),
    (dt.date(2026, 3, 31), 9900),
    (dt.date(2026, 4, 1), 51000),
]

# Labels exactly as they arrive from a spreadsheet export.
IMPORTED_LABELS = ["FY 2025-26", "2025-2026", "2025/26", "FY 25", "2025-2027"]


def show(title):
    print(f"\n{title}\n{'-' * len(title)}")


def main():
    show("Which fiscal year does each date belong to?")
    for day, _ in LEDGER:
        print(
            f"  {day.isoformat()}  ->  {fy_label(fy_of(day))}"
            f"   month {month_of_fy(day):>2}/12"
            f"   {quarter_label(fy_of(day), quarter_of(day))}"
        )

    show("Totals by fiscal year")
    totals = {}
    for day, amount in LEDGER:
        totals[fy_of(day)] = totals.get(fy_of(day), 0) + amount
    for year in sorted(totals):
        print(f"  {fy_label(year, 'long'):<12} Rs {totals[year]:>10,}")

    show("FY 2025-26 at a glance")
    start, end = fy_bounds(2025)
    print(f"  runs        {start.isoformat()} to {end.isoformat()}")
    print(f"  days        {fy_days(2025)}")
    print(f"  quarters    {[q[0].isoformat() for q in fy_quarters(2025)]}")
    print(f"  label       {fy_label(2025)}, {fy_label(2025, 'plain')}, "
          f"{fy_label(2025, 'compact')}")

    show("Reading labels from an export")
    for label in IMPORTED_LABELS:
        try:
            year = parse_fy(label)
        except FiscalYearError as error:
            print(f"  {label:<12} -> rejected: {error}")
        else:
            print(f"  {label:<12} -> {year} ({fy_label(year)})")

    show("A report that crosses 1 April")
    print(f"  fiscal years in range: {fy_span(dt.date(2025, 12, 1), dt.date(2026, 5, 1))}")


if __name__ == "__main__":
    main()
