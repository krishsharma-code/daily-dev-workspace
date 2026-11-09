# india-id-kit

Validation, normalisation and masking for the Indian identifiers that turn up
in every signup, KYC and checkout form: Aadhaar, PAN, GSTIN, IFSC, UPI IDs,
mobile numbers, PIN codes and vehicle registrations. Standard library only —
no `jsonschema`, no third-party validator, no regex you have to trust for a
checksum.

Every validator has the same shape, so a form handler needs one branch:

```python
from india_ids import validate, normalize, mask, is_valid

result = validate("gstin", " 27aapfu0939f1zv ")
result.ok                                  # True
result.value                               # '27AAPFU0939F1ZV'
result.details["state"]                    # 'Maharashtra'
result.details["pan"]                      # 'AAPFU0939F'

validate("pan", "ABCD1234").reason         # 'PAN must be 10 characters, got 8'
is_valid("aadhaar", "2345 6789 0124")      # True  (Verhoeff checksum)
mask("aadhaar", "234567890124")            # 'XXXX XXXX 0124'
normalize("pan", "abcpa1234f")             # 'ABCPA1234F', or raises ValidationError
```

## What each kind checks

| kind | rules enforced |
| --- | --- |
| `aadhaar` | 12 digits after stripping spaces and hyphens, first digit 2-9, **Verhoeff checksum** over all 12 digits |
| `pan` | `AAAAA9999A` shape, and position 4 must be a real holder-type letter (`ABCFGHJLPT`) |
| `gstin` | 15 characters, base-36 charset, numeric state code present in the GST state table, embedded PAN shape, entity number 1-9/A-Z, `Z` in position 14, **base-36 check character** |
| `ifsc` | `AAAA0XXXXXX`, fifth character is `0`, bank and branch codes split out |
| `vpa` | exactly one `@`, 3-255 character handle from `A-Z a-z 0-9 . _ -`, PSP handle that starts with a letter; known PSPs are named, unknown ones are still accepted |
| `mobile` | 10 digits starting 6-9 after stripping `+91`, `91`, `0`, `0091`, spaces, dashes and brackets; returns E.164 |
| `pincode` | 6 digits, no leading zero, postal region from the first digit and sorting circle from the first two |
| `vehicle` | standard `SS RR [SERIES] NNNN` plates with a real state/UT code, and BH-series `YYBHNNNNSS` plates |

Two checksum helpers are exported on their own if you need them elsewhere:
`aadhaar_check_digit("23456789012")` → `"4"` and
`gstin_check_char("29AAGCB7383J1Z")` → `"4"`.

## API

| Name | Purpose |
| --- | --- |
| `validate(kind, value)` | Returns a `Result`. Never raises on bad input. |
| `is_valid(kind, value)` | Boolean shorthand. |
| `normalize(kind, value)` | Returns the canonical value or raises `ValidationError`. |
| `mask(kind, value)` | Display-safe form; raises on invalid input so nothing is half-masked into looking real. |
| `Result` | `kind`, `ok`, `value`, `reason`, `details`; truthy when valid; `.unwrap()` for the value-or-raise path. |
| `ValidationError` | `ValueError` subclass carrying `kind`, `value` and `reason`. |
| `KINDS` | The eight supported kinds, in order. |

`kind` also accepts the aliases `aadhar`, `gst`, `upi`, `upi-id`, `phone`,
`pin`, `pin-code`, `postal-code`, `rc`, `registration` and `number-plate`, in
any case. An unknown kind raises `ValueError` — that is a bug in the caller,
not something a user typed.

### Design notes

- Bad user input is a *value*, not an exception. `validate` returns
  `ok=False` with a short reason you can show in a form field.
- `reason` explains the **first** problem found, so a nine-character PAN says
  "10 characters, got 9" rather than "bad shape".
- `details` is only filled for valid identifiers and carries what you would
  otherwise re-parse: the GST state name, the PAN holder type, the UPI
  provider, the RTO code.
- Integers are accepted (a PIN code often arrives as a number); booleans,
  floats and other objects are rejected with a clear message.
- Nothing is logged or echoed in full by `mask`: Aadhaar shows the last four
  digits, PAN hides the first five characters, GSTIN keeps the state code and
  the check character, mobile keeps the last four digits.

## Run the demo

```bash
cd python/india-id-kit
python example.py
```

It prints the canonical value and details for one sample of each kind, the
rejection reason for one bad sample of each kind, the Verhoeff check digit in
action, a GSTIN broken into its parts, and the masking table.

## Run the tests

```bash
cd python/india-id-kit
python -m pytest
```

273 tests: dispatch and aliases, `Result` truthiness and `unwrap`, every
non-string and blank input shape, the Verhoeff check digit against generated
numbers and a sweep of every single-digit typo in a valid Aadhaar, all ten PAN
holder types plus the rejected letters, GSTIN state/PAN/entity/`Z`/check-digit
rules with two published sample GSTINs, IFSC shape, UPI handle and PSP rules
with known and unknown providers, every mobile prefix spelling, PIN region
derivation, standard and BH-series plates including legacy state codes, the
mask table, and an AST check that the module imports nothing outside the
standard library. `pytest` is needed only to run the suite.
