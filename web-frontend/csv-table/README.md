# csv-table

A dependency-free CSV reader and writer that runs unchanged in the browser and
in Node. It follows RFC 4180 where that matters — quoted fields, doubled quotes,
CRLF — and copes with what real exports throw at it: a byte-order mark, `;` or
tab delimiters, ragged rows, blank lines.

```js
import { parseCsv, stringifyCsv } from "./csv-table.js";

const { headers, rows } = parseCsv('city,note\nPune,"cultural capital, of Maharashtra"\n');
// headers → ["city", "note"]
// rows    → [["Pune", "cultural capital, of Maharashtra"]]

stringifyCsv(rows, { headers });
// "city,note\nPune,\"cultural capital, of Maharashtra\"\n"
```

## What it does

| Member | Purpose |
| --- | --- |
| `parseCsv(text, options)` | Parse into `{ delimiter, headers, rows }`. `headers` is `null` when `header: false`. |
| `parseRows(text, options)` | Lower level: `{ delimiter, rows }`, every record as an array of strings. |
| `stringifyCsv(rows, options)` | Write arrays (or objects, given `headers`) back to CSV text. |
| `detectDelimiter(text)` | Guess between `,` `;` tab and `\|` from the document itself. |
| `toObjects(headers, rows)` | Map parsed rows onto plain objects keyed by header. |
| `fromObjects(objects)` | Turn plain objects into `{ headers, rows }`, inferring the header order. |
| `CsvParseError` | Error thrown on malformed input, carrying `line` and `column`. |

### Parse options

| Option | Default | Meaning |
| --- | --- | --- |
| `delimiter` | `"auto"` | A character, or `"auto"` to detect. Also accepts `"comma"`, `"semicolon"`, `"tab"`, `"pipe"`. |
| `quote` | `'"'` | Quote character. |
| `header` | `true` | Treat the first record as the header (used by `parseCsv`). |
| `trim` | `false` | Trim surrounding whitespace on **unquoted** fields. |
| `skipEmptyLines` | `true` | Drop records with no content. |
| `strict` | `false` | Throw instead of recovering from malformed input. |

### Write options

| Option | Default | Meaning |
| --- | --- | --- |
| `delimiter` | `","` | Field separator. |
| `quote` | `'"'` | Quote character. |
| `newline` | `"\n"` | Line ending; `"\r\n"` for Excel-friendly output. |
| `quoteAll` | `false` | Quote every field, not just the ones that need it. |
| `headers` | `null` | Written as the first line, and used to pick keys from object rows. |

## Behaviour worth knowing

- **Delimiter detection is consistency-based, not frequency-based.** Each
  candidate is scored by how many records it splits into the same number of
  fields, so a comma inside a quoted sentence cannot outvote a real tab. If no
  candidate splits anything, `","` is used.
- **Quotes are only written when needed** — a delimiter, a quote, a line break,
  or surrounding whitespace. `quoteAll: true` opts into quoting everything.
- **`""` and an empty field are the same value** once parsed, so the writer
  emits the shorter form. The data round-trips; the bytes may not.
- **Short rows are padded** with empty strings to the header width. Longer rows
  are kept as they are and `toObjects` maps only the columns the header names —
  nothing is silently dropped. `strict: true` turns any width mismatch into a
  `CsvParseError` instead.
- **Empty and duplicate header names are repaired**: `,,` becomes
  `column2`, `column3`, and a repeated `name` becomes `name_2`.
- A stray quote inside an unquoted field (`5" screen`) is kept literally unless
  `strict` is set, where it throws.
- Errors carry a 1-based `line` and `column` so a bad row can be pointed at.

## Layout

```
web-frontend/csv-table/
├── README.md
├── csv-table.js          # implementation (ES module)
├── csv-table.css         # styles for the demo
├── index.html            # runnable paste-and-preview demo
├── package.json          # `npm test` runs the suite
└── csv-table.test.mjs    # node:test suite (42 tests)
```

## Try the demo

The demo is an ES module, so serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/csv-table
python3 -m http.server 8000
# then open http://localhost:8000/
```

The sample is a small table of Indian metros with a quoted field on every row.
Switch the delimiter to semicolon or tab and watch the auto-detection change its
mind; untick *First row is a header* to see every record as data; tick *Strict*
and break a quote to see the error position.

## Run the tests

```bash
cd web-frontend/csv-table
npm test          # or: node --test
```

42 tests, no dependencies — `node:test` and `node:assert` only.
