# inr-money

Indian rupee amounts, done the Indian way: lakh/crore digit grouping,
half-up rounding, tolerant parsing, and the amount spelled out in words for a
cheque. Standard library only — `Decimal`, `re`, no dependencies.

`"₹{:,.2f}".format(12345678.9)` gives `₹12,345,678.90`, which is wrong in
India. The correct grouping is `₹1,23,45,678.90`: last three digits, then
groups of two. This module gets that right, and answers the other three
questions an invoice or payment screen asks — *what does the user's text
mean?*, *how do I round it?*, and *how do I write it out in words?*

```python
from decimal import Decimal
from inr_money import format_inr, parse_inr, amount_to_words, integer_to_words

format_inr(12345678.9)                  # '₹1,23,45,678.90'
format_inr(-1234, accounting=True)      # '(₹1,234.00)'
format_inr(1234567, decimals=0)         # '₹12,34,567'
format_inr(Decimal("99999.999"))        # '₹1,00,000.00'

parse_inr("₹ 1,23,456.78")              # Decimal('123456.78')
parse_inr("2.5 lakh")                   # Decimal('250000')
parse_inr("(1,234.50)")                 # Decimal('-1234.50')

integer_to_words(1234567)               # 'Twelve Lakh Thirty-Four Thousand Five Hundred Sixty-Seven'
amount_to_words(1234.5)                 # 'One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise Only'
```

## API

| Name | Purpose |
| --- | --- |
| `format_inr(amount, *, decimals=2, symbol='₹', space=False, accounting=False)` | Indian-grouped currency string. `symbol=None` for bare digits, `accounting=True` for `(₹1,234.00)` negatives. |
| `parse_inr(text)` | `Decimal` from text: currency prefix/suffix, lakh/crore multiplier, Indian or Western commas, signs, parentheses. |
| `group_digits(digits)` | Just the grouping: `'1234567'` → `'12,34,567'`. |
| `integer_to_words(n)` | Non-negative integer in the Indian scale (crore, lakh, thousand, hundred). |
| `amount_to_words(amount, *, rupees='Rupee', rupees_plural=None, paise='Paise', paise_plural=None, include_paise=True, include_only=True)` | Cheque wording, rounded to paise first. |
| `MoneyError` | `ValueError` subclass raised for anything unparseable or out of range. |

`format_inr` and `amount_to_words` accept `int`, `float`, `Decimal`, or a string
that `parse_inr` understands.

## Behaviour worth knowing

- **Money is `Decimal`.** A `float` argument is converted through `str()`, so
  `0.1 + 0.2` formats as `₹0.30` rather than the binary expansion.
- **Rounding is half-up**, the invoice convention: `format_inr(1234.5,
  decimals=0)` is `₹1,235`, where Python's `round()` would give `1234`.
  `Decimal("0.005")` is `₹0.01`.
- **Negative zero is never printed.** `-0.004` formats as `₹0.00`, not
  `-₹0.00`.
- **`paise` is invariant** — `'Fifty Paise'`, not `'Fifty Paises'`. Pass
  `paise_plural=` if you need something else; `rupees_plural=` likewise.
- **Parsing never guesses.** `''`, `'abc'`, `'1.2.3'`, `'12 rupees'`,
  `'1 lakh crore'` all raise `MoneyError`. Commas and spaces between digits are
  separators and are dropped, so `'1 23 456'` reads as `123456`.
- **Words use the Indian scale**, not short-scale millions: `10**9` is
  `One Hundred Crore`, and the crore step recurses, so `10**12` is
  `One Lakh Crore`.
- **Input limits.** `decimals` must be `0`–`8`; a `bool` is rejected as an
  amount; `NaN` and infinity raise.

## Run the example

```bash
cd python/inr-money
python example.py
```

It prints the grouping table, a small invoice total built from messy price
strings, the same total in five renderings, the parse results for the input
people actually type, the half-up rounding cases, both word forms, and the
error message for each kind of malformed input.

## Run the tests

```bash
cd python/inr-money
python -m pytest
```

165 tests: every grouping boundary from 1 to 13 digits, formatting at 0–4
decimals with rounding that carries into the grouping, half-up versus
half-even, float conversion, negative zero, symbol/space/accounting options,
round-tripping `format_inr` through `parse_inr`, every accepted and rejected
parse shape (prefixes, suffixes and their multipliers, `lakh` never read as
`k`, parentheses, nested signs, unbalanced brackets, non-finite values),
Indian-scale words for every magnitude including crore recursion, cheque
wording with singular and plural nouns and paise rounding, and a guard that the
implementation contains no true division — money is never computed in binary
floating point. `pytest` is needed only to run the suite.
