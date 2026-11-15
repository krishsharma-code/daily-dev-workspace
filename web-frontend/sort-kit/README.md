# sort-kit

A small, dependency-free set of comparators and a stable multi-key sorter for
tables, lists and record sets. It runs unchanged in the browser and in Node.

It exists because sorting a real column is rarely `(a, b) => a.x - b.x`. The
awkward cases are handled here, once:

- **Multi-key sorting** with a per-key direction — "team ascending, then salary
  descending" is a single call, not three chained sorts.
- **Blank placement that ignores direction.** Missing values stay last (or
  first) whether the column is ascending or descending, so reversing a column
  never drags every empty cell to the top.
- **Natural ordering**, so `TCK-9` sorts before `TCK-10` and `report9.txt`
  before `report10.txt`.
- **A total order for mixed types.** Numbers, booleans, dates and strings have
  a fixed rank, so one stray string in a numeric column cannot make the order
  depend on the engine's comparison path.
- **Stability.** Equal rows keep their input order, so successive sorts behave
  like a spreadsheet.

Nothing is mutated and nothing is cloned: `sortBy` returns a new array holding
your original items.

## What it does

| Member | Purpose |
| --- | --- |
| `sortBy(items, keys, options?)` | Sorted **copy** of `items`; stable; input untouched. |
| `createComparator(keys, options?)` | A `(a, b) => number` comparator over whole items, for `Array.prototype.sort` or anywhere else. |
| `compareValues(a, b, options?)` | Compare two scalars with type ranking and blank handling. |
| `compareNatural(a, b, options?)` | Natural (digit-aware) string comparison. |
| `createCollator(options?)` | Shared, cached `Intl.Collator` built from a small options bag. |

### Keys

A key is any of:

| Form | Meaning |
| --- | --- |
| `"salary"` | Property of the item. |
| `"user.name"` | Dotted path; a missing branch reads as blank. |
| `(item) => item.tags.length` | Accessor function. |
| `{ key, direction, nulls, type }` | Descriptor. `by` may be used instead of `key` for an accessor. |

`keys` may be one key or an array of them, tried left to right. The first key
that separates two items decides; `sortBy` breaks remaining ties by original
position.

### Options

Every option can be set globally (third argument) or per key (in a descriptor):

| Option | Values | Default | Notes |
| --- | --- | --- | --- |
| `direction` | `"asc"`, `"desc"` | `"asc"` | Applies to the key's value comparison only, not to blank placement. |
| `nulls` | `"first"`, `"last"` | `"last"` | `null`, `undefined` and `NaN` all count as blank. |
| `type` | `"auto"`, `"string"`, `"natural"`, `"number"` | `"auto"` | `"number"` coerces (`"88500"` → `88500`) and treats unparsable or empty values as blank. |
| `numeric` | `true`, `false` | `false` | Applies natural ordering to `"auto"` string keys. |
| `locale` | BCP 47 tag(s) | runtime default | Passed to `Intl.Collator`, so `en-IN` collation is a one-liner. |
| `sensitivity` | `"base"`, `"accent"`, `"case"`, `"variant"` | `"variant"` | `"base"` makes the comparison case- and accent-insensitive. |
| `caseFirst` | `"upper"`, `"lower"`, `false` | ICU default | Passed through to `Intl.Collator`. |

### Type ranking

When `type` is `"auto"` and a column holds more than one type, values are
ordered by rank, then within rank:

```
false < true  <  numbers  <  dates  <  strings  <  anything else (by String())
```

Two invalid `Date`s compare equal rather than returning `NaN`, which would make
the sort order undefined.

## Layout

```
web-frontend/sort-kit/
├── README.md
├── sort-kit.js            # implementation (ES module)
├── sort-kit.css           # styles for the demo
├── index.html             # runnable sortable-table demo
├── package.json           # `npm test` runs the suite
└── sort-kit.test.mjs      # node:test suite
```

## Try the demo

The demo is an ES module, so serve the folder over HTTP rather than opening the
file directly (module scripts are blocked on `file://`):

```bash
cd web-frontend/sort-kit
python3 -m http.server 8000
# then open http://localhost:8000/
```

Click a header to sort by it, click again to reverse, and <kbd>Shift</kbd>+click
another header to add a secondary key (the badge shows the priority). The
*Salary* column has two blanks and one value stored as text; switch **Blank
cells** between *always last* and *always first* and reverse the column to see
that the blanks do not follow the direction. Switch **Sort type** to *natural*
to sort the ticket references by their numbers. The panel at the bottom shows
the exact `sortBy` call being made.

## Run the tests

```bash
cd web-frontend/sort-kit
node --test
```

34 tests, no third-party dependencies; Node 18+ is enough.

## Example

```js
import { sortBy, compareNatural } from "./sort-kit.js";

const staff = [
  { name: "Asha", team: "Platform", salary: 94000 },
  { name: "Bilal", team: "Design", salary: 72000 },
  { name: "Chetan", team: "Platform", salary: null },
];

// Team ascending, then salary descending. The blank salary stays last.
sortBy(staff, [
  "team",
  { key: "salary", direction: "desc" },
]);

// A numeric column whose values arrive as strings, with blanks preserved.
sortBy(staff, { key: "salary", type: "number", nulls: "last" });

// Natural order for identifiers: TCK-9, TCK-10, TCK-100.
sortBy([{ id: "TCK-10" }, { id: "TCK-9" }], { key: "id", type: "natural" });

// A comparator you can hand to anything that takes one.
[...staff].sort((a, b) => compareNatural(a.name, b.name));
```

## Behaviour notes

- **`sortBy` never mutates the input** and returns a new array, but the items
  inside it are the same objects — it sorts references, it does not clone.
- **Stability is guaranteed**, not inherited from the engine: `sortBy` decorates
  each item with its original index and uses that as the final tiebreak.
- **Blank placement is absolute.** `direction` reverses value comparisons only.
  This matches spreadsheet behaviour, where reversing a column keeps empty
  cells at the bottom.
- **`null`, `undefined` and `NaN` are the same thing to the sorter.** `NaN`
  cannot participate in a comparison, so treating it as blank is what keeps the
  order total.
- **`type: "number"` does not guess at currency or thousands separators.** It
  uses `Number()`, so `"1,200"` is blank; strip separators in your accessor
  (`by: (row) => Number(row.amount.replace(/,/g, ""))`) if you need that.
- **Collators are cached** by their configuration, because constructing an
  `Intl.Collator` per comparison would dominate the cost of sorting.
- **Bad input fails loudly.** An unknown `direction`, `nulls` or `type`, a key
  that is both `key` and `by`, or a non-array passed to `sortBy` throws a
  `TypeError`/`RangeError` with the offending position in the message, rather
  than silently sorting nothing.
- **Ties between identical natural strings are broken by length**, so `"7"`
  sorts before `"007"` instead of the two swapping places on every run.
