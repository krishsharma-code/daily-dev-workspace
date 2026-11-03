# Feature Flags

A tiny, self-hosted feature-flag service for local development. Define a flag,
give it a rollout percentage and an allowlist, then ask the server whether a
given user should see it — the answer is deterministic, so the same user always
gets the same decision.

No dependencies — just the Python standard library and plain HTML/CSS/JS.

## Why

Shipping a change behind a flag is easy; testing one is not, because the
provider's SDK, network and account all sit between you and the decision. This
tool keeps the whole thing local: the flags, the bucketing and the API are all
in one folder you can read, so you can see exactly why a user was included or
excluded.

## Layout

```
feature-flags/
├── README.md
├── client/          # vanilla HTML/CSS/JS console (no build step)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/          # stdlib HTTP server + SQLite storage
    ├── app.py       # routing and static file serving
    ├── store.py     # flag storage and evaluation
    ├── conftest.py
    └── tests/
        ├── test_api.py
        └── test_store.py
```

## Run it

```bash
python server/app.py                  # http://127.0.0.1:8010, in-memory database
python server/app.py --db flags.db    # persist across restarts
python server/app.py --port 9000
```

Then open <http://127.0.0.1:8010> for the console, or drive the API directly:

```bash
# create a flag, off for everyone by default
curl -X POST http://127.0.0.1:8010/api/flags \
  -H 'Content-Type: application/json' \
  -d '{"key": "new-checkout", "description": "v2 checkout", "rollout": 20}'

# flip it on, allowlist a teammate, and widen the rollout
curl -X PATCH http://127.0.0.1:8010/api/flags/new-checkout \
  -H 'Content-Type: application/json' \
  -d '{"enabled": true, "rollout": 40, "allowlist": ["alice"]}'

# ask for one decision
curl 'http://127.0.0.1:8010/api/evaluate?key=new-checkout&user_id=user-42'
# {"key":"new-checkout","user_id":"user-42","enabled":false,"reason":"outside_rollout","bucket":6800,"rollout":40}

# or every flag at once
curl 'http://127.0.0.1:8010/api/evaluate/all?user_id=alice'
```

## How a decision is made

1. **Kill switch.** If `enabled` is false, the flag is off for everyone —
   allowlist included.
2. **Allowlist.** If the user id is in the flag's allowlist, it is on.
3. **Rollout.** Otherwise `sha256("<key>:<user_id>")` is mapped onto one of
   10,000 buckets; the flag is on when the bucket falls below
   `rollout × 100`.

The hash includes the flag key, so one user is spread differently across flags
(two 10% rollouts do not hit the same 10% of people) while staying fixed per
flag. Nothing is stored per user, so evaluation is a pure function — the same
inputs always give the same answer, and you can reproduce any decision by hand.

`reason` reports which rule fired: `disabled`, `allowlist`, `rollout` or
`outside_rollout`. `bucket` is the user's 0–9999 slot, which makes "why did
this user miss a 20% rollout?" answerable: their bucket was ≥ 2000.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | `{"status": "ok", "flags": n}` |
| `GET` | `/api/flags` | Every flag, sorted by key |
| `POST` | `/api/flags` | Create or replace a flag (201 new, 200 existing) |
| `GET` | `/api/flags/<key>` | One flag |
| `PATCH` | `/api/flags/<key>` | Update `description`, `enabled`, `rollout` or `allowlist` |
| `DELETE` | `/api/flags/<key>` | Delete a flag (204) |
| `GET` | `/api/evaluate?key=&user_id=` | One decision |
| `POST` | `/api/evaluate` | One decision: `{"key": "...", "user_id": "..."}` |
| `GET` | `/api/evaluate/all?user_id=` | Every decision for one user |

A flag body is `{"key", "description", "enabled", "rollout", "allowlist"}`.
`key` is required and normalised to lowercase; it may hold 1–64 characters of
letters, digits, `.`, `-` and `_`. `rollout` is an integer 0–100 (a boolean is
rejected rather than read as 1). `allowlist` is a list of user ids, deduped with
order kept; a comma-separated string is accepted for convenience.

`POST` keeps any field you omit on an existing flag, so a partial body is a
safe upsert. Errors are JSON with an `error` string: `400` for a malformed
body or key, `404` for an unknown flag, `422` for a bad field value, `405` for
the wrong method.

## Tests

```bash
cd server && python -m pytest -q
```

`route_request` and `Flag.evaluate` are pure functions, so the API and the
bucketing rules are tested without sockets; one end-to-end test boots a real
`ThreadingHTTPServer` to check the API and static file serving together.

## Scope

This is a local development tool. It binds to `127.0.0.1` by default, has no
authentication, and every caller may edit flags. Do not expose it to an
untrusted network, and do not treat it as an authorisation system — it decides
what to *show*, never what a user is *allowed* to do.
