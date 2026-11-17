# Ranked Choice

Count ranked ballots under instant-runoff voting, round by round, without a
spreadsheet and without trusting a black box.

Paste a list of rankings (`Aditi > Bharat > Chen`), press count, and the server
returns every round: the tallies, who was eliminated and why, how many ballots
ran out of preferences, and whether the winner also beats everyone head-to-head.

No dependencies: Python standard library on the server, plain HTML/CSS/JS in the
browser.

## Why

Ranked-choice counts are easy to get subtly wrong. The three things that decide
real elections are exactly the three a hand count hides:

* **Transfers.** When a candidate is eliminated, their ballots move to the next
  living preference — not to the next name written, which may already be out.
* **Exhaustion.** A ballot that ranked only eliminated candidates stops
  counting, and the majority needed to win drops with it.
* **Ties.** Two candidates level for last place means somebody has to choose who
  goes out. This tool never chooses silently: it reports the tie and the rule it
  applied.

It also reports the **pairwise (Condorcet) result** alongside the runoff, because
the two can disagree — instant-runoff can elect someone who loses a head-to-head
to another candidate. Seeing both side by side is the point.

## Layout

```
ranked-choice/
├── README.md
├── client/          # vanilla HTML/CSS/JS console (no build step)
│   ├── index.html
│   ├── app.js
│   └── styles.css
└── server/          # stdlib HTTP server
    ├── app.py       # routing and static file serving
    ├── core.py      # parsing, round-by-round counting, pairwise analysis
    ├── conftest.py
    └── tests/
        ├── test_core.py
        └── test_api.py
```

## Run it

```bash
python server/app.py                  # http://127.0.0.1:8021
python server/app.py --port 9100
```

Open <http://127.0.0.1:8021>, hit **Load example**, then **Count the vote**. The
server also serves `client/`, so everything runs from one origin.

### Ballot format

One ballot per line, most preferred first, separated by `>` or `,`:

```
# comments and blank lines are ignored
Bengaluru > Goa > Jaipur > Kochi
Jaipur, Goa, Bengaluru
3: Goa > Kochi              # a weight prefix counts this line three times
Kochi                       # ranks one candidate and then stops
```

A line may rank as few or as many candidates as it likes. Names are matched to
the roster case-insensitively; the roster's spelling is what the report uses.
When no roster is supplied, names are taken from the ballots in first-appearance
order.

### API

`POST /api/tabulate`

```bash
curl -X POST http://127.0.0.1:8021/api/tabulate \
  -H 'Content-Type: application/json' \
  -d '{
        "candidates": ["Bengaluru", "Goa", "Jaipur", "Kochi"],
        "ballots": "Bengaluru > Goa > Jaipur > Kochi\nBengaluru > Jaipur > Goa\nBengaluru > Jaipur > Goa\nBengaluru > Goa > Jaipur\nGoa > Bengaluru > Jaipur\nGoa > Bengaluru > Kochi\nGoa > Kochi > Bengaluru\nJaipur > Goa > Bengaluru\nJaipur > Bengaluru > Goa\nKochi"
      }'
```

| Field | Default | Notes |
| --- | --- | --- |
| `ballots` | `""` | Required in practice: a string of ranked lines, as above. |
| `candidates` | derived | A list of names or a comma/newline separated string. Every ballot name must appear here; at most 50 candidates. |

Limits: 50 candidates, 10 000 ballot lines, weight 1–1 000 000 per line, 1 MiB
request body.

The response for that payload (the middle round is elided here, the real one
returns all three):

```json
{
  "winner": "Bengaluru",
  "tied": [],
  "reason": "majority",
  "candidates": ["Bengaluru", "Goa", "Jaipur", "Kochi"],
  "rounds": [
    {
      "round": 1,
      "tally": {"Bengaluru": 4, "Goa": 3, "Jaipur": 2, "Kochi": 1},
      "active_votes": 10,
      "exhausted": 0,
      "majority_needed": 6,
      "eliminated": "Kochi",
      "tie_break": [],
      "winner": null
    },
    {
      "round": 3,
      "tally": {"Bengaluru": 5, "Goa": 4},
      "active_votes": 9,
      "exhausted": 1,
      "majority_needed": 5,
      "eliminated": null,
      "tie_break": [],
      "winner": "Bengaluru"
    }
  ],
  "ballots": {"lines": 10, "weight": 10, "distinct_rankings": 10},
  "first_preferences": {"Bengaluru": 4, "Goa": 3, "Jaipur": 2, "Kochi": 1},
  "pairwise": {
    "Bengaluru": {"Goa": 5, "Jaipur": 5, "Kochi": 3},
    "Goa": {"Bengaluru": 4, "Jaipur": 3, "Kochi": 4},
    "Jaipur": {"Bengaluru": 2, "Goa": 4, "Kochi": 2},
    "Kochi": {"Bengaluru": 1, "Goa": 0, "Jaipur": 0}
  },
  "condorcet": "Bengaluru",
  "condorcet_agrees": true
}
```

| Field | Meaning |
| --- | --- |
| `winner` | The elected candidate, or `null` when the count ends in a dead heat. |
| `reason` | `majority`, `last_standing` or `tie`. |
| `tied` | The candidates still level when no winner emerged. |
| `rounds[].tally` | Votes per continuing candidate in that round. |
| `rounds[].majority_needed` | `floor(active / 2) + 1` — a strict majority of votes still counting. |
| `rounds[].exhausted` | Weight of ballots whose whole ranking is out. |
| `rounds[].tie_break` | Names tied for last place, in break order. Empty when the elimination was unambiguous. |
| `pairwise[a][b]` | Weight of ballots ranking `a` above `b`. |
| `condorcet` | The candidate who beats every other head-to-head, or `null`. |
| `condorcet_agrees` | Whether the runoff elected that same candidate (`null` if either is absent). |

Other endpoints: `GET /api/health`, `GET /api/example` (the payload behind the
client's example button). Errors come back as `400` with `{"error": "..."}`
naming the offending field or line — `line 3: 'Divya' is not a declared
candidate`, for instance.

## How the count works

1. Every ballot is credited to its highest-ranked candidate who is still in the
   race. Ballots with nobody left are counted as exhausted.
2. A candidate holding **more than half of the votes still counting** wins.
   Exhausted ballots therefore lower the bar: 10 ballots with 1 exhausted need 5,
   not 6.
3. Otherwise the candidate with the fewest votes is eliminated — one candidate
   per round, so every transfer is visible in the record.
4. If the remaining candidates are all level, the count stops and reports the
   tie rather than inventing a winner.

**Breaking a tie for last place.** When several candidates share the lowest
tally, the count eliminates the one with the fewest **first-preference** votes,
then the one that sorts first alphabetically. The round record lists the tied
names in `tie_break`, so an arbitrary-looking elimination is never hidden.

**What this is not.** Single-seat IRV only: no multi-winner STV, no surplus
transfers, no equal-preference (tied) rankings within a ballot — each ballot is a
strict order. Ballot secrecy and audit trails are out of scope; this is a
counting tool, not an election system.

## Tests

```bash
cd server
python -m pytest
```

74 tests cover ballot parsing and its error messages, roster validation,
first-round majorities, transfers, skipped preferences, exhausted ballots
lowering the majority bar, both tie-break rules, dead heats, weighted ballots,
the pairwise matrix, Condorcet winners and cycles, and the routing layer with a
bad-request table.
