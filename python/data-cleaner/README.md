# data-cleaner

A small, dependency-free Python toolkit for cleaning *tabular records* — the
plain `dict`s you get from `csv.DictReader`, a spreadsheet export, or a JSON
API. Every helper is pure: it returns a new value and never mutates its input,
so a cleaning pipeline stays easy to reason about and to test.

## What it does

| Function | Purpose |
| --- | --- |
| `normalize_whitespace(value)` | Trim and collapse internal runs of whitespace. |
| `is_missing(value)` | Treat `None`, `""`, `"N/A"`, `"null"`, `"-"`, … as absent. |
| `to_number(value)` | Coerce to a finite `int`/`float`, handling `"1,234"`, `"₹1,200"`, `"12.0"`. Non-finite values (`nan`, `inf`, `"1e400"`) return `None`. |
| `normalize_phone(value)` | Normalise Indian numbers to `+91XXXXXXXXXX`. |
| `normalize_email(value)` | Trim, lower-case, and validate an address. |
| `clean_record(record, ...)` | Clean one record, with optional per-field transformers. |
| `clean_records(records, ...)` | Clean a batch, optionally dropping all-empty rows. |
| `dedupe_records(records, ...)` | Drop duplicates, keeping the first; compare by whole record or chosen key fields. |
| `missing_required(record, fields, *, missing_tokens=...)` | List required fields that are absent, using the same missing-token set as `clean_record`. |

## Layout

```
python/data-cleaner/
├── README.md
├── datacleaner.py            # implementation
├── example.py                # runnable demo
├── conftest.py               # makes the module importable in tests
└── tests/
    └── test_datacleaner.py
```

## Run the demo

```bash
cd python/data-cleaner
python example.py
```

## Run the tests

```bash
cd python/data-cleaner
python -m pytest
```

No third-party dependencies are required at runtime; `pytest` is needed only to
run the test suite.

## Example

```python
from datacleaner import clean_records, dedupe_records, normalize_phone, to_number

raw = [
    {"name": "  Ada   Lovelace ", "phone": "98765 43210", "amount": "1,200"},
    {"name": "N/A", "phone": "", "amount": ""},
]

clean = clean_records(
    raw,
    transformers={"phone": normalize_phone, "amount": to_number},
)
clean = dedupe_records(clean, key_fields=["phone"])
```

`clean_records` trims whitespace, turns `"N/A"`/empty values into `None`, applies
the transformers to the values that remain, and (by default) drops rows where
every field ended up empty.

## Behaviour notes

- `to_number` treats "not finite" as "not a number": `nan`/`inf` (as values or as
  strings such as `"inf"` or an overflow like `"1e400"`) coerce to `None`, so a
  single bad cell can't poison a downstream sum or average.
- `dedupe_records` compares whole records order-independently and never raises on
  mixed-type or non-comparable keys; it keys nested structures by `str(key)`.
