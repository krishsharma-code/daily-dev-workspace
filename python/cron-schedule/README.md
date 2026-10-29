# cronschedule

Parse five-field cron expressions and work out when they fire next — without
shelling out to `crontab` or pulling in a scheduler.

The interesting part is not reading `* * * * *`; it is getting the awkward
cases right and making them testable:

- **No minute-by-minute scan.** The search jumps to the next allowed month,
  then the next matching day, then the next allowed hour and minute. A
  leap-day schedule starting in March 2028 answers instantly.
- **Impossible schedules say so.** `0 0 31 2 *` raises `ScheduleExhausted`
  instead of looping or quietly returning `None`.
- **Classic day semantics.** When *both* day-of-month and day-of-week are
  restricted, the schedule fires when *either* matches, as Vixie cron does.
  When only one is restricted, only that one counts.
- **Time is an argument, never a global.** Every query takes the `datetime` to
  search from, so tests are deterministic and a scheduler can ask "what did I
  miss?" by walking `iter_after` backwards from a stored watermark.
- **Errors name the field.** `CronError` messages say which field and which
  value, e.g. `hour field: 24 is outside 0-23`.

No third-party packages. Python 3.9+.

## Run it

```bash
cd python/cron-schedule
python example.py        # demo: describe schedules, list upcoming runs
python -m pytest -q      # 81 tests
```

## Use it

```python
import datetime as dt
from cronschedule import CronExpression, ScheduleExhausted

schedule = CronExpression("0 9-17 * * MON-FRI")
schedule.describe()
# 'at minute 0, past hours 9-17, on Monday, Tuesday, Wednesday, Thursday, Friday'

schedule.next_after(dt.datetime(2026, 10, 29, 21, 2))
# datetime(2026, 10, 30, 9, 0)

schedule.iter_after(dt.datetime(2026, 10, 29, 21, 2), 3)
# (2026-10-30 09:00, 2026-10-30 10:00, 2026-10-30 11:00)

CronExpression("0 9 13 * FRI").describe()
# 'at minute 0, past hour 9, on day(s) 13 of the month or on Friday'

try:
    CronExpression("0 0 31 2 *").next_after(dt.datetime(2026, 10, 29))
except ScheduleExhausted as error:
    print(error)   # '0 0 31 2 *' has no fire time within 2928 days of ...
```

A tiny poller built on it:

```python
import time
from cronschedule import CronExpression

schedule = CronExpression("*/15 * * * *")
while True:
    upcoming = schedule.next_after(dt.datetime.now())
    time.sleep(max(0.0, (upcoming - dt.datetime.now()).total_seconds()))
    run_job()
```

## API

| Call | Returns |
| --- | --- |
| `CronExpression(expr)` | parsed schedule; raises `CronError` on bad input |
| `.expression` | normalised five-field text |
| `.minutes`, `.hours`, `.days_of_month`, `.months`, `.days_of_week` | `frozenset` of allowed values |
| `matches(moment)` | `True` if the expression fires during that minute |
| `next_after(moment)` | first fire time strictly after `moment` |
| `iter_after(moment, count)` | tuple of the next `count` fire times |
| `describe()` | short English rendering of the schedule |

`next_after` and `iter_after` accept naive or aware datetimes; an aware value
keeps its `tzinfo`. Non-`datetime` arguments raise `TypeError`.

## Expression syntax

`minute hour day-of-month month day-of-week`, plus the usual macros.

| Form | Meaning | Example |
| --- | --- | --- |
| `*` | every value | `* * * * *` |
| `5` | one value | `5 * * * *` |
| `1-5` | inclusive range | `0 9-17 * * *` |
| `*/15` | step from the field minimum | `*/15 * * * *` |
| `5-20/5` | step inside a range | `5-20/5 * * * *` |
| `50/5` | step from a value to the field maximum | `50/5 * * * *` |
| `9,17` | list | `0 9,17 * * *` |
| `jan`, `mon` | three-letter names (case-insensitive) | `0 0 * jan-mar mon-fri` |
| `7` | alias for Sunday (`0`) | `0 0 * * 7` |
| `@daily` `@hourly` `@weekly` `@monthly` `@yearly` `@midnight` `@annually` | macros | `@daily` |

Ranges are validated: reversed ranges (`17-9`), zero or non-numeric steps,
empty list terms, and out-of-range values are all rejected with a message
naming the field.

## Semantics worth knowing

- **Day-of-month vs day-of-week.** Both restricted → OR. One restricted → that
  one alone. Neither → every day. `0 0 13 * FRI` fires on the 13th *and* on
  every Friday.
- **A field written as `*` is unrestricted; `*/1` is not.** `*/1` expands to
  every value but still counts as restricted, which changes the day rule above
  — write `*` when you mean "no constraint".
- **`matches` is minute-level.** It ignores seconds and microseconds, so
  `09:30:47` matches a `30 9 * * *` schedule. `next_after` truncates the start
  time to the minute before adding one.
- **Timezone arithmetic is wall-clock.** An aware datetime keeps its `tzinfo`
  and is advanced by calendar arithmetic; no DST shift is applied. Give it a
  UTC datetime if you need absolute time.
- **Search horizon is eight years**, which always contains a 29 February. Past
  that, `ScheduleExhausted` is raised — that only happens for schedules that
  can never fire, such as `31 February` or `30 February`.

## Layout

```
python/cron-schedule/
├── README.md
├── conftest.py                    # puts this folder on sys.path for pytest
├── cronschedule.py                # the implementation
├── example.py                     # runnable demo
└── tests/
    └── test_cronschedule.py
```
