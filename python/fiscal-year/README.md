# fiscal-year

Indian fiscal-year and quarter arithmetic: which FY a date belongs to, its
bounds, fiscal quarters, labels and tolerant parsing of labels like
`FY 2025-26`. Standard library only — `datetime`, no dependencies.

India's financial year runs 1 April to 31 March, so `date.year` is wrong for
every January, February and March date in a reporting codebase. `fy_of()`
returns the year the fiscal year **starts** in, which is how FY labels are
written: FY 2025-26 covers 1 April 2025 to 31 March 2026.

```python
from datetime import date
from fiscal_year import fy_of, fy_label, quarter_of, quarter_bounds, parse_fy

fy_of(date(2026, 2, 20))        # 2025  (still FY 2025-26)
fy_label(2025)                  # 'FY2025-26'
quarter_of(date(2025, 8, 1))    # 2     (Q2 is Jul-Sep)
quarter_bounds(2025, 4)         # (date(2026, 1, 1), date(2026, 3, 31))
parse_fy('FY 25-26')            # 2025
```

## API

| Name | Purpose |
| --- | --- |
| `fy_of(value)` | Start year of the fiscal year containing a date or datetime. |
| `fy_bounds(year)` | `(1 April, 31 March)` of that fiscal year, inclusive. |
| `quarter_of(value)` | Fiscal quarter 1-4: Q1 Apr-Jun, Q2 Jul-Sep, Q3 Oct-Dec, Q4 Jan-Mar. |
| `quarter_bounds(year, quarter)` | Inclusive `(start, end)` dates of one fiscal quarter. |
| `month_of_fy(value)` | Month index inside the fiscal year, April = 1 to March = 12. |
| `fy_label(year, style='short')` | `'FY2025-26'`, `'FY2025-2026'`, `'2025-26'` or `'FY25'`. |
| `quarter_label(year, quarter, style='short')` | `'Q4 FY2025-26'`. |
| `parse_fy(text)` | Start year from a label, or an `int` passed through. |
| `shift_fy(year, periods)` | Move forward or back by whole fiscal years. |
| `fy_months(year)` | The 12 `(calendar_year, month)` pairs, April first. |
| `fy_quarters(year)` | The four quarter `(start, end)` pairs. |
| `fy_days(year)` | 365 or 366, counting the leap day correctly. |
| `fy_span(start, end)` | Every fiscal year touched by a date range. |
| `FiscalYearError` | `ValueError` subclass raised for bad dates, years, quarters and labels. |

### Behaviour worth knowing

- **Boundaries are inclusive.** `fy_of(date(2026, 3, 31))` is `2025` and
  `fy_of(date(2026, 4, 1))` is `2026`.
- **`datetime` is accepted** anywhere a date is, and truncated to its day.
  Anything else — a string, a float, `None` — raises `FiscalYearError` rather
  than being guessed at.
- **`parse_fy` is strict about spans.** `2025-2026` and `2025/26` are fine;
  `2025-2027`, `2025-25` and `26-25` raise. En and em dashes, stray spaces
  and mixed case are tolerated.
- **Two-digit labels are read as 20xx.** `'FY99'` is 2099, not 1999, because a
  compact label carries no century. Use four-digit labels for older years.
- **Years are limited to 1000-9998** so that every label and the 31 March that
  closes the year stay representable.

## Run it

```bash
cd python/fiscal-year
python example.py        # demo: a ledger bucketed by fiscal year
python -m pytest         # 120 tests
```
