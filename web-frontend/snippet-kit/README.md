# snippet-kit

Search-result snippets without the usual sharp edges. Given a long text and a
query, it returns a short window around the best match, with the matched spans
reported as offsets, so you can render them with `<mark>`, with your own
framework, or as a ready-made escaped HTML string.

It is a pure ES module over strings: no DOM, no dependencies, no build step. It
runs unchanged in the browser, in Node and in a bundler.

## What it handles that a naive `indexOf` loop does not

- **Queries with phrases.** `"gst invoice" refund` searches one phrase and one
  word; unquoted multi-word queries search each word, so `credit note` returns
  two ranges — one per word — while `"credit note"` returns one.
- **Merged highlights.** Overlapping and touching matches collapse into one
  span, so a word is never wrapped in two `<mark>`s.
- **Unicode that survives.** Offsets stay valid on Devanagari, Tamil and emoji,
  because case folding never changes the string length.
- **Cuts on boundaries.** The window never starts or ends mid-word, and never
  splits a surrogate pair (🧾) or orphans a combining mark.
- **Escaped output.** `highlightHtml` and `snippetHtml` escape `& < > " '`, so a
  snippet of user content can never inject markup.
- **A useful no-match result.** A query that misses still yields the opening
  words of the text with a trailing ellipsis, rather than an empty string.

## Use it

```js
import { snippet, snippetHtml, annotate, findMatches } from "./snippet-kit.js";

const body =
  "Customer raised a ticket because the GST invoice total differs from the " +
  "purchase order. Support asked billing to regenerate the GST invoice and " +
  "issue a credit note for the gap.";

snippet(body, '"credit note"', { length: 90 });
// {
//   text: "Support asked billing to regenerate the GST invoice and issue a credit note for the gap.",
//   ranges: [{ start: 64, end: 75 }],
//   leadingEllipsis: true,
//   trailingEllipsis: false,
//   matched: 1,
// }

snippetHtml(body, '"credit note"', { length: 90 });
// …Support asked billing to regenerate the GST invoice and issue a
// <mark>credit note</mark> for the gap.

snippetHtml(body, "credit note", { length: 90 });
// …issue a <mark>credit</mark> <mark>note</mark> for the gap.   (two terms)

annotate(body, "credit note");              // [{ text, match }] segments
findMatches(body, "GST");                   // [{ start: 37, end: 40 }, { start: 128, end: 131 }]
```

## API

| Member | Purpose |
| --- | --- |
| `parseQuery(query)` | `{ text, phrase }[]` from a query string; quoted phrases kept whole, duplicates dropped, longest first. |
| `findMatches(text, terms, options?)` | Merged, sorted, non-overlapping `{ start, end }` ranges (end exclusive). |
| `countMatches(text, terms, options?)` | Number of merged ranges. |
| `annotate(text, terms, options?)` | Alternating `{ text, match }` segments covering the whole string. |
| `escapeHtml(value)` | Escape `& < > " '` for HTML text or an attribute value. |
| `highlightHtml(text, terms, options?)` | Escaped HTML with each match wrapped in a tag (default `<mark>`). |
| `snippet(text, terms, options?)` | Match-centred window: `{ text, ranges, leadingEllipsis, trailingEllipsis, matched }`. |
| `snippetHtml(text, terms, options?)` | `snippet()` rendered to escaped HTML, ellipses included. |

`terms` is either a query string or an array of strings / `{ text, phrase }`
objects, so a query you parsed once can be reused across many documents.

### Match options

| Option | Default | Meaning |
| --- | --- | --- |
| `caseSensitive` | `false` | Compare case-sensitively. |
| `wholeWord` | `false` | Reject matches glued to a letter, digit or `_`, so `gst` does not match inside `gstr`. |

### Snippet options

| Option | Default | Meaning |
| --- | --- | --- |
| `length` | `160` | Window budget in characters. The result may be slightly longer when a match straddles an edge; a match longer than the budget is shown whole. |
| `ellipsis` | `"…"` | What `snippetHtml` puts at a trimmed end. |
| `normalizeWhitespace` | `true` | Collapse runs of whitespace to single spaces before cutting, so line breaks from HTML do not eat the budget. |
| `tag` | `"mark"` | Element name used by the HTML renderers. |
| `className` | — | Optional class on that element; escaped. |

## Behaviour worth knowing

- **Ranges are offsets, not markup.** `ranges` is re-based onto the returned
  `text`, so `text.slice(range.start, range.end)` is the matched substring.
- **`leadingEllipsis` / `trailingEllipsis` are facts, not strings.** They say
  whether that side of the text was trimmed, so a caller that renders with a
  framework can put its own affordance there.
- **The window favours the first match** and opens about a third of the way in.
  If a match would be cut by an edge, the window widens to include it and trims
  the roomier side back to budget.
- **Word snapping is best-effort within 24 characters.** Text with no spaces at
  all (a long URL, a base64 blob) is cut at the character limit instead of
  being dropped.
- **Case folding is length-preserving.** Characters whose lowercase form changes
  length (such as `İ`) are compared as they are; every offset stays correct.
- **No highlighting is not an error.** `snippet` with no match returns the head
  of the text, `matched: 0` and `ranges: []`.

## Run it

```bash
cd web-frontend/snippet-kit
node --test              # 30 tests
open index.html          # interactive demo over six sample tickets
```

The demo searches a small corpus of support tickets, including a Devanagari
one, and prints the ranges and segments the first result returned.

## Files

```
snippet-kit/
├── README.md
├── index.html
├── package.json
├── snippet-kit.css
├── snippet-kit.js
└── snippet-kit.test.mjs
```
