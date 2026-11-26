# business-hours

Business-hours aware scheduling for anything with a working calendar: support
SLA deadlines, "reply within 4 working hours", payroll cut-offs, delivery
promises. Closed time is skipped, never counted — weekends, holidays and lunch
breaks included. Pure standard library (`datetime` + `zoneinfo`).

```python
from datetime import datetime
from business_hours import BusinessCalendar

desk = BusinessCalendar(("09:30", "18:30"), holidays=["2026-11-27"], tz="Asia/Kolkata")

desk.add_business_hours(datetime(2026, 11, 26, 17, 0), 8)
# -> datetime(2026, 11, 30, 16, 0, tzinfo=ZoneInfo('Asia/Kolkata'))

desk.business_time_between(datetime(2026, 11, 26, 18, 0), datetime(2026, 11, 30, 11, 0))
# -> timedelta(hours=2)

desk.next_open(datetime(2026, 11, 28, 12, 0))   # Saturday -> Monday 09:30
```

## API

| Member | Purpose |
| --- | --- |
| `BusinessCalendar(hours, *, workdays, holidays, tz)` | One window per working day. `hours` is `("09:30", "18:30")`, `workdays` uses Monday = `0`. |
| `BusinessCalendar.from_windows(windows, *, holidays, tz)` | Different windows per weekday, and more than one per day: `{0: [("09:00", "13:00"), ("14:00", "18:00")]}`. |
| `add_business_hours(start, hours)` | Advance by open time. `hours` is an int, a float or a `timedelta`; negative values walk backwards. |
| `business_time_between(start, end)` | Open time inside a range, as a `timedelta`. |
| `is_open(moment)` | Is the moment inside a window? The closing time itself is closed. |
| `next_open(moment, *, inclusive=True)` | First open moment at or after `moment`; `inclusive=False` skips the window you are already in. |
| `is_workday(day)` | Open weekday and not a holiday. |
| `segments(day)` | The day's `(start, end)` intervals, already resolved against holidays. |
| `holidays` | The closed dates, as a `frozenset` of `date`. |

Hours accept `"09:30"`, `"9:30"`, `"09:30:00"` or a `datetime.time`.
Holidays accept `date`, `datetime` or `"YYYY-MM-DD"`. Windows that overlap or
touch are merged, so double-booked shift definitions count time once.

## Behaviour worth knowing

- **The close is exclusive.** 17:00 in a 09:00–17:00 calendar is closed, so
  `add_business_hours(17:00, 1)` lands at 10:00 the next working morning.
- **A moment before the open is clipped, not charged.** 07:00 plus one hour
  gives 10:00: the wait for the doors to open is not working time.
- **Holidays close the whole day.** Put a holiday on a weekend and nothing changes.
- **Overnight windows are rejected.** A window whose close is not after its open
  raises `ValueError` rather than guessing at midnight rollover.
- **Timezones.** With `tz=` set, naive input is read as wall-clock time in that
  zone and results come back zone-aware; aware input is converted into it. With
  `tz=None` the calendar works on naive datetimes and rejects aware ones instead
  of silently comparing across zones. India has no DST, so a fixed 09:30–18:30
  means the same thing all year.
- **Search limit.** Arithmetic walks at most `MAX_DAYS` (about ten years) of days
  before raising `NoOpenWindowError`, a `ValueError` subclass. It is only
  reachable with a calendar whose windows never match the input.

## Run it

```bash
cd python/business-hours
python example.py        # prints an SLA deadline walk-through
python -m pytest         # 45 tests
```

## Files

```
business-hours/
├── README.md
├── business_hours.py
├── example.py
├── conftest.py
└── tests/
    └── test_business_hours.py
```
