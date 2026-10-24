# Flashcards

A tiny, self-hosted spaced-repetition study app. Add cards, review the ones
that are due, and the scheduler decides when each one comes back. The whole
thing runs on the Python standard library — no framework, no build step, no
third-party packages.

```
flashcards/
├── README.md
├── client/            # front end (HTML + CSS + vanilla JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/            # back end (stdlib HTTP server + SQLite)
    ├── app.py         # router + static file server
    ├── srs.py         # the SM-2 scheduling algorithm (pure)
    ├── store.py       # SQLite-backed card store
    ├── example.py     # runnable tour of the scheduler (no sockets)
    └── tests/         # pytest suite
```

## Run it

From this folder:

```bash
python server/app.py
```

Then open <http://127.0.0.1:8000>. The server serves the API *and* the front
end from the same origin, so nothing else is needed.

Configuration via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLASHCARDS_HOST` | `127.0.0.1` | Bind address |
| `FLASHCARDS_PORT` | `8000` | Port |
| `FLASHCARDS_DB` | `:memory:` | SQLite path (e.g. `cards.db` to persist) |

To keep your cards between runs:

```bash
FLASHCARDS_DB=cards.db python server/app.py
```

You can also open `client/index.html` directly in a browser; when it is loaded
from `file://` it falls back to talking to `http://localhost:8000`.

## How scheduling works

`server/srs.py` implements a small variant of
[SuperMemo 2](https://en.wikipedia.org/wiki/SuperMemo#Description_of_SM-2_algorithm).
Each card tracks an **ease factor**, an **interval** (in days) and a
**repetition** streak. When you review a card you give it a quality from `0`
(total blackout) to `5` (perfect recall):

- A quality below `3` is a **lapse**: the streak resets, the interval drops to
  `1` day, and the ease is docked by `0.2` (never below `1.3`).
- A successful review grows the interval: `1` day, then `6` days, then
  `previous_interval × ease` (rounded). The ease rises for quality `5`, stays
  put for `4`, and falls for `3`.

The next review date is `now + interval`. The scheduler is a pure function of
`(state, quality, now)` — `now` is injected rather than read from the clock, so
behaviour is fully deterministic and easy to test.

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check, total cards and due count |
| `GET` | `/api/decks` | Per-deck summary: total and due counts |
| `GET` | `/api/cards` | List cards, oldest first. Optional `?deck=` |
| `POST` | `/api/cards` | Create a card from `{ "front", "back", "deck" }` |
| `GET` | `/api/cards/due` | Cards due now, oldest first. Optional `?deck=` |
| `GET` | `/api/cards/{id}` | Fetch one card |
| `POST` | `/api/cards/{id}/review` | Grade a card with `{ "quality": 0..5 }` |
| `DELETE` | `/api/cards/{id}` | Delete a card |

Examples:

```bash
curl -s localhost:8000/api/cards \
  -H 'Content-Type: application/json' \
  -d '{"front":"What is the capital of Japan?","back":"Tokyo","deck":"geography"}'

curl -s localhost:8000/api/cards/due

curl -s localhost:8000/api/cards/1/review \
  -H 'Content-Type: application/json' \
  -d '{"quality":5}'
```

`front` and `back` are required. `deck` is optional and defaults to
`default`; deck names are trimmed and have internal whitespace collapsed.

## Test it

```bash
pip install pytest
cd server && python -m pytest -q
```

The suite covers the scheduler (interval growth, ease updates, lapses and
validation), the store (validation, filtering, due queries, persistence) and
the HTTP router (every route and status code), plus one end-to-end test that
boots a real server on an ephemeral port and exercises it over HTTP.

## Try the scheduler without a socket

```bash
python server/example.py
```
