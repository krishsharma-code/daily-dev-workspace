# fuzzy-matcher

A tiny, dependency-free fuzzy string matcher and ranker for the browser and
Node. Give it a query and some text, and it tells you whether the query's
characters appear **in order** inside the text (a subsequence match), how good
that match is, and *where* it landed so you can highlight it.

It is the ranking engine behind a typeahead: `"fb"` should surface `"Foo Bar"`
above `"foobar"`, and `"newd"` should still find `"New Delhi"`.

## What it does

| Export | Purpose |
| --- | --- |
| `fuzzyMatch(query, text, options)` | Match one string; returns `{ score, indices }` or `null`. |
| `fuzzyScore(query, text, options)` | Just the score (`-Infinity` on no match) — handy in a comparator. |
| `search(items, query, options)` | Rank an array of strings/objects, best match first. |
| `highlightSegments(text, indices)` | Split text into `{ text, match }` runs for highlighting. |
| `WEIGHTS` | The scoring weights, exported so you can re-rank for your domain. |

## Scoring

Matches are rewarded for being **consecutive**, landing on a **word boundary**
(after a space, `-`, `_`, `.`, `/`, …), sitting on a **camelCase** transition,
and starting at the very beginning of the text. Gaps inside the matched span and
longer texts carry small penalties. The weights are all in `WEIGHTS` and can be
overridden per call, so `search(items, q, { weights: { boundary: 0 } })` re-ranks
without touching the library.

Matching is case-insensitive by default; pass `{ caseSensitive: true }` to opt out.

## Layout

```
web-frontend/fuzzy-matcher/
├── README.md
├── index.html          # runnable interactive demo
├── fuzzy.js            # the library (ES module)
├── fuzzy.css           # demo styles
├── fuzzy.test.mjs      # Node test suite (built-in test runner)
└── package.json        # marks the folder as an ES module for Node
```

## Try the demo

Open `index.html` in a browser — no build step, no dependencies. Type a city
name or abbreviation and watch the matches rank and highlight live.

## Run the tests

The tests use Node's built-in test runner, so there is nothing to install:

```bash
cd web-frontend/fuzzy-matcher
node --test
```

## Usage

```js
import { search, highlightSegments } from "./fuzzy.js";

const people = [
  { name: "Ada Lovelace", city: "London" },
  { name: "Alan Turing", city: "Manchester" },
];

// Rank across chosen fields; each result keeps the matched indices.
const results = search(people, "ada", { keys: ["name", "city"], limit: 10 });
// → [{ item: { name: "Ada Lovelace", … }, score: 20, key: "name", indices: [0,1,2] }]

// Turn the indices into highlighted markup.
const parts = highlightSegments("Ada Lovelace", results[0].indices);
// → [{ text: "Ada", match: true }, { text: " Lovelace", match: false }]
```

`search` options: `keys` (fields to match, defaulting to the item itself),
`limit`, `threshold` (drop weak matches), `caseSensitive`, and `weights`.

No third-party dependencies at runtime; the library is a single ES module you
can also load straight from the browser with `<script type="module">`.
