# Streak Board

Track a handful of habits and see the only number that matters: how many due
days in a row you have actually done.

The whole thing runs on the Python standard library — no framework, no build
step, no third-party packages. Dates are stored as plain `YYYY-MM-DD` days
(never timestamps), so a check-in at 23:59 and one at 00:01 are different days,
and every streak question has one unambiguous answer.

```
streak-board/
├── README.md
├── client/               # front end (HTML + CSS + vanilla JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/               # back end (stdlib HTTP server + SQLite)
    ├── app.py            # router + static file server
    ├── streak.py         # the streak engine: weekdays, runs, completion
    ├── store.py          # SQLite-backed habits and check-ins
    ├── example.py        # runnable tour of the engine (no sockets)
    └── tests/            # pytest suite
```

## Run it

From this folder:

```bash
python server/app.py
```

Then open <http://127.0.0.1:8012>. The server serves the API *and* the front end
from the same origin, so nothing else is needed (open the page through the
server, not as a `file://` path).

Configuration via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `STREAK_BOARD_HOST` | `127.0.0.1` | Bind address |
| `STREAK_BOARD_PORT` | `8012` | Port |
| `STREAK_BOARD_DB` | `:memory:` | SQLite path (e.g. `habits.db` to persist) |

To keep habits between runs:

```bash
STREAK_BOARD_DB=habits.db python server/app.py
```

## How a streak is counted

A habit is **due** on the weekdays you pick (Monday is `0`, Sunday is `6`), and
only those days can extend or break a streak. Three rules cover every case:

1. A due day with a check-in extends the run.
2. A due day in the **past** with no check-in ends it — that is the whole point
   of a streak.
3. **Today, if due and not ticked yet, is skipped rather than counted as a
   miss.** The day is not over. Checking in later today then extends the run
   instead of restarting it at 1.

Check-ins on days that are not due are kept in the history (so the calendar
shows what happened) but never change the streak. The `longest` streak is
computed over the whole history, so it survives a reset of `current`.

Alongside the two streaks, each habit reports completion for the last 30 days:
`done` out of `scheduled`, plus the exact days that were missed. A Saturday-only
habit is measured against Saturdays, not against 30 calendar days, so a
100% week is genuinely 100%.

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check and habit count |
| `GET` | `/api/habits` | The board: every habit, its history and its summary |
| `GET` | `/api/board` | Same payload as `/api/habits` |
| `POST` | `/api/habits` | Create a habit from `{ "name", "weekdays" }` |
| `GET` | `/api/habits/{id}` | One habit with history and summary |
| `DELETE` | `/api/habits/{id}` | Delete a habit and its check-ins |
| `GET` | `/api/habits/{id}/checkins` | The habit's check-ins, oldest first |
| `POST` | `/api/habits/{id}/checkins` | Check in for `{ "day", "note" }` |
| `DELETE` | `/api/habits/{id}/checkins/{day}` | Undo a check-in |

A habit id is a slug of its name, so `"Read 20 pages"` becomes `read-20-pages`.
`weekdays` accepts `"daily"`, `"mon,wed,fri"`, `["Mon", "Wed"]`, `[0, 2, 4]` or
omitted (every day). `day` defaults to today and may be backfilled, but never
dated in the future — a streak that can be pre-filled says nothing about whether
anything was done.

Every route accepts `?today=YYYY-MM-DD` to pin the reference day, which is how
the tests stay reproducible without touching the clock.

Examples:

```bash
# Create a habit due Mon/Wed/Fri
curl -s localhost:8012/api/habits \
  -H 'Content-Type: application/json' \
  -d '{"name":"Gym","weekdays":"mon,wed,fri"}'

# Tick off a day (repeat calls are a no-op, not an error)
curl -s localhost:8012/api/habits/gym/checkins \
  -H 'Content-Type: application/json' \
  -d '{"day":"2026-11-25","note":"leg day"}'

# Undo it
curl -s -X DELETE localhost:8012/api/habits/gym/checkins/2026-11-25

# The whole board, as of a fixed day
curl -s 'localhost:8012/api/habits?today=2026-11-25'
```

A habit in the board payload looks like this:

```json
{
  "id": "gym",
  "name": "Gym",
  "weekdays": [0, 2, 4],
  "created_at": "2026-11-25T15:30:00+00:00",
  "days": ["2026-11-20", "2026-11-23", "2026-11-25"],
  "summary": {
    "current": 3,
    "longest": 3,
    "total_checkins": 3,
    "done_today": true,
    "scheduled_today": true,
    "window": {
      "start": "2026-10-27",
      "end": "2026-11-25",
      "days": 30,
      "done": 3,
      "scheduled": 13,
      "missed": ["2026-10-28", "..."],
      "percent": 23.1
    }
  }
}
```

Errors are JSON: `400` for a malformed body, `404` for an unknown habit or
check-in, `405` for the wrong method, and `422` with a human-readable `error` for
anything the validator rejects (a blank name, an empty weekday set, a check-in
dated tomorrow).

## Test it

```bash
pip install pytest
cd server && python -m pytest -q
```

The suite covers the engine (weekday sets, gaps, the "today is still open"
grace, unscheduled check-ins, completion over partial weeks), the store
(validation, idempotent check-ins, cascade delete, persistence across reopen)
and every route — plus end-to-end tests that boot a real server on an ephemeral
port to check the round trip, CORS preflight and static file serving.

## Try the engine without a socket

```bash
python server/example.py
```
