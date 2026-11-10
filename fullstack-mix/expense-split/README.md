# Expense Split

Split shared bills without losing a paisa. Create a group, log who paid what,
and get back net balances plus the shortest list of payments that settles
everyone up.

The whole thing runs on the Python standard library — no framework, no build
step, no third-party packages. Money is stored as integer **paise**, never as a
float, so a split always adds back up to exactly what was paid.

```
expense-split/
├── README.md
├── client/               # front end (HTML + CSS + vanilla JS)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/               # back end (stdlib HTTP server + SQLite)
    ├── app.py            # router + static file server
    ├── splitter.py       # money parsing, the three split rules, debt simplification
    ├── store.py          # SQLite-backed groups, expenses and settlement
    ├── example.py        # runnable tour of the engine (no sockets)
    └── tests/            # pytest suite
```

## Run it

From this folder:

```bash
python server/app.py
```

Then open <http://127.0.0.1:8011>. The server serves the API *and* the front end
from the same origin, so nothing else is needed (open the page through the
server, not as a `file://` path).

Configuration via environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `EXPENSE_SPLIT_HOST` | `127.0.0.1` | Bind address |
| `EXPENSE_SPLIT_PORT` | `8011` | Port |
| `EXPENSE_SPLIT_DB` | `:memory:` | SQLite path (e.g. `trips.db` to persist) |

To keep groups between runs:

```bash
EXPENSE_SPLIT_DB=trips.db python server/app.py
```

## How the money works

Three ways to split a bill:

| Type | What it means | Example |
| --- | --- | --- |
| `equal` | Divide as evenly as possible; leftover paise go to the participants named first | ₹100 across three → 33.34, 33.33, 33.33 |
| `exact` | State each share; they must add up to the amount paid | ₹900 → 300 + 600 |
| `weights` | Proportional shares (2:1:1, or percentages) allocated by largest remainder | ₹1000 at 2:1:1 → 500, 250, 250 |

Amounts are accepted as `"1200"`, `"1200.5"`, `"1,23,456.78"`, `"₹2400"` or
`"Rs. 99.99"`. A float is rejected outright — by the time a number reaches the
server as a float, precision is already gone, and quietly rounding somebody's
money is worse than refusing it. Output uses Indian digit grouping
(`₹1,00,00,000.00`).

A balance is the net of what each person paid and what they owe: positive means
the group owes them, negative means they owe the group, and the balances always
sum to zero. `simplify_debts` then settles the group greedily — the largest
debtor pays the largest creditor each round — which needs at most `n - 1`
transfers for `n` people.

## HTTP API

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness check and group count |
| `GET` | `/api/groups` | List groups |
| `POST` | `/api/groups` | Create a group from `{ "name", "members" }` |
| `GET` | `/api/groups/{id}` | One group, with its expense count and total |
| `DELETE` | `/api/groups/{id}` | Delete a group and its expenses |
| `POST` | `/api/split` | Preview a split without saving it |
| `GET` | `/api/groups/{id}/expenses` | List the group's expenses |
| `POST` | `/api/groups/{id}/expenses` | Add an expense |
| `DELETE` | `/api/groups/{id}/expenses/{n}` | Remove an expense |
| `GET` | `/api/groups/{id}/settlement` | Balances plus the settle-up plan |

A group id is a slug of its name, so `"Goa Trip"` becomes `goa-trip`. Members
must be unique and non-blank; a group needs at least two of them.

Examples:

```bash
# Create a group
curl -s localhost:8011/api/groups \
  -H 'Content-Type: application/json' \
  -d '{"name":"Goa Trip","members":["asha","rahul","meera"]}'

# Split equally (the default)
curl -s localhost:8011/api/groups/goa-trip/expenses \
  -H 'Content-Type: application/json' \
  -d '{"description":"Beach shack dinner","payer":"asha","amount":"2400"}'

# State exact shares
curl -s localhost:8011/api/groups/goa-trip/expenses \
  -H 'Content-Type: application/json' \
  -d '{"description":"Cab from airport","payer":"rahul","amount":"900",
       "split":{"type":"exact","shares":{"asha":"300","rahul":"600"}}}'

# Weighted, 2:2:1
curl -s localhost:8011/api/groups/goa-trip/expenses \
  -H 'Content-Type: application/json' \
  -d '{"description":"Villa (2 nights)","payer":"meera","amount":"6000",
       "split":{"type":"weights","weights":{"asha":2,"rahul":2,"meera":1}}}'

# Who pays whom
curl -s localhost:8011/api/groups/goa-trip/settlement
```

The settlement response looks like this:

```json
{
  "group_id": "goa-trip",
  "total_paise": 930050,
  "total": "9,300.50",
  "balances": {
    "asha": { "paise": -110025, "amount": "-1,100.25" },
    "rahul": { "paise": -259975, "amount": "-2,599.75" },
    "meera": { "paise": 370000, "amount": "3,700.00" }
  },
  "settlements": [
    { "from": "rahul", "to": "meera", "amount": 259975, "amount_display": "2,599.75" },
    { "from": "asha", "to": "meera", "amount": 110025, "amount_display": "1,100.25" }
  ]
}
```

Errors are JSON: `400` for a malformed body, `404` for an unknown group or
expense, `405` for the wrong method, and `422` with a human-readable `error` for
anything the validator rejects (a non-member payer, shares that do not add up, a
blank description, an amount of zero).

## Test it

```bash
pip install pytest
cd server && python -m pytest -q
```

142 tests cover the splitting engine (exact arithmetic, remainder allocation,
randomised ledgers that must always sum back), the store (validation, cascade
delete, persistence across reopen) and every route, plus end-to-end tests that
boot a real server on an ephemeral port to check CORS headers and static file
serving.

## Try the engine without a socket

```bash
python server/example.py
```
