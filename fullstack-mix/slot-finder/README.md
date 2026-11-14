# Slot Finder

Find the first free meeting slots when everyone's calendar is somewhere else.

Paste each person's busy blocks as `start/end` pairs with a UTC offset, give the
window and working hours, and the server returns the earliest slots that fit the
meeting — no calendar access, no accounts, no third-party SDK.

No dependencies: Python standard library on the server, plain HTML/CSS/JS in the
browser.

## Why

"Sometime next week?" is the expensive question of the working day. The usual
answers are a scheduling SaaS that wants read access to every calendar, or a
trawl through a grid that ignores time zones. This tool takes the one thing that
is genuinely portable — a list of busy intervals with explicit offsets — and
answers the question locally, in a folder you can read end to end.

Because every instant carries an offset, a colleague in London pasting
`2026-11-17T08:30+00:00` blocks the right 14:00 IST hour in the other person's
day, and a DST change is handled by the `zoneinfo` database instead of by
arithmetic on wall-clock times.

## Layout

```
slot-finder/
├── README.md
├── client/          # vanilla HTML/CSS/JS console (no build step)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/          # stdlib HTTP server
    ├── app.py       # routing and static file serving
    ├── core.py      # parsing, interval maths, slot search
    ├── conftest.py
    └── tests/
        ├── test_core.py
        └── test_api.py
```

## Run it

```bash
python server/app.py                  # http://127.0.0.1:8020
python server/app.py --port 9000
```

Open <http://127.0.0.1:8020>, hit **Load example**, then **Find slots**. The
server also serves `client/`, so everything runs from one origin.

### API

`POST /api/slots`

```bash
curl -X POST http://127.0.0.1:8020/api/slots \
  -H 'Content-Type: application/json' \
  -d '{
        "busy": "2026-11-16T09:30+05:30/2026-11-16T10:00+05:30\n2026-11-16T14:00+05:30/2026-11-16T15:30+05:30",
        "timezone": "Asia/Kolkata",
        "start_date": "2026-11-16",
        "end_date": "2026-11-18",
        "work_start": "09:00",
        "work_end": "18:00",
        "weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "duration_min": 45,
        "step_min": 15,
        "limit": 12
      }'
```

| Field | Default | Notes |
| --- | --- | --- |
| `busy` | `""` | One `start/end` pair per line; blank lines and `#` comments ignored. Every instant needs an offset (`+05:30`, `Z`). |
| `timezone` | `Asia/Kolkata` | IANA name; working hours and returned times are in this zone. |
| `start_date`, `end_date` | required | `YYYY-MM-DD`, inclusive, at most 62 days apart. |
| `work_start`, `work_end` | `09:00`, `18:00` | Wall-clock in `timezone`. |
| `weekdays` | Mon–Fri | `0..6` (Mon = 0) or names like `"mon"`, mixed freely. |
| `duration_min` | `30` | 1–1440. |
| `step_min` | `15` | Candidate grid spacing, 1–240. |
| `limit` | `20` | Stop after this many slots, 1–500. |
| `earliest` | — | Optional ISO instant; slots starting before it are dropped ("from now on"). |

The response carries `slots` (local `start`/`end` plus `start_utc`/`end_utc`),
a per-day `days` breakdown with `free_minutes`, and `truncated` / `complete`
flags so a capped result never looks like an empty calendar.

Other endpoints: `GET /api/health`, `GET /api/example` (the payload the client's
example button uses). Errors come back as `400` with `{"error": "..."}` naming
the offending field or line.

## How the search works

1. Each day in the window is turned into a working-hours window in the requested
   zone, then converted to UTC. A DST transition therefore shrinks or lengthens
   the real window correctly — 00:00–06:00 on a spring-forward day is five
   hours, not six.
2. Busy blocks are merged and clipped to that window, and subtracted from it.
3. Inside each free stretch, candidate starts sit on a grid **anchored at the
   working day's start**, so 09:00–18:00 with a 15-minute step offers 09:00,
   09:15, 09:30, … — free time at 09:40 offers 09:45, never 09:40. Slots are
   emitted in time order and stop at `limit`.

Only `add`-style, identity-free reasoning is used: a block that merely touches
the window edge is not treated as busy, and overlapping blocks are merged first,
so the same minute is never counted twice.

## Tests

```bash
cd server
python -m pytest
```

51 tests cover parsing (including the error messages), interval arithmetic, the
grid anchoring, cross-time-zone busy blocks, the DST case, weekday selection,
validation, and the routing layer with a bad-request table.
