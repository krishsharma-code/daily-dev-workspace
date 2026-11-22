"""Indian rupee money handling: lakh/crore grouping, parsing, and amount in words.

Indian digit grouping is not the Western one. ``12,345,678.90`` is wrong here;
the same amount is ``1,23,45,678.90`` — the last three digits, then groups of
two (thousand, lakh, crore). Every ``str.format`` with ``,`` gets this wrong,
and so does every "₹{:,.2f}" you have ever written.

This module is the dependency-free fix for three jobs that come up in any
Indian invoice, receipt or payment screen:

    from inr_money import format_inr, parse_inr, amount_to_words

    format_inr(12345678.9)              # '₹1,23,45,678.90'
    format_inr(-1234, accounting=True)  # '(₹1,234.00)'
    parse_inr("2.5 lakh")               # Decimal('250000')
    amount_to_words(1234.5)             # 'One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise Only'

Design notes
------------
* Money is ``Decimal``, never ``float``. A ``float`` argument is accepted for
  convenience but is converted through ``str()`` so ``0.1 + 0.2`` formats as
  ``0.30`` instead of the binary expansion.
* Rounding is ``ROUND_HALF_UP``, the convention on Indian invoices, not
  Python's default banker's rounding. ``format_inr(1234.5, decimals=0)`` is
  ``'₹1,235'``, not ``'₹1,234'``.
* ``-0.004`` rounds to a plain ``'₹0.00'``. A negative zero never reaches the
  output, because ``'-₹0.00'`` on an invoice looks like a bug.
* ``parse_inr`` treats its input as untrusted text: separators are stripped,
  a currency prefix/suffix and a lakh/crore multiplier are understood, and
  anything left that is not a number raises ``MoneyError`` rather than
  returning a guess.
* Words follow the Indian numbering system (crore, lakh, thousand, hundred),
  not short-scale "billion"/"million", and are the form a cheque or a
  payment advice expects.
"""

from __future__ import annotations

import math
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__all__ = [
    "MoneyError",
    "amount_to_words",
    "format_inr",
    "group_digits",
    "integer_to_words",
    "parse_inr",
]

DEFAULT_SYMBOL = "\u20b9"
"""The rupee sign. Pass ``symbol=None`` to ``format_inr`` to leave it out."""

MAX_DECIMALS = 8
"""Beyond this a quantize is more likely a caller bug than a requirement."""

_MULTIPLIERS = (
    ("crores", Decimal(10) ** 7),
    ("crore", Decimal(10) ** 7),
    ("crs", Decimal(10) ** 7),
    ("cr", Decimal(10) ** 7),
    ("lakhs", Decimal(10) ** 5),
    ("lakh", Decimal(10) ** 5),
    ("lacs", Decimal(10) ** 5),
    ("lac", Decimal(10) ** 5),
    ("thousands", Decimal(10) ** 3),
    ("thousand", Decimal(10) ** 3),
    ("k", Decimal(10) ** 3),
)
"""``(suffix, factor)``, longest suffix first so ``lakh`` never matches as ``k``."""

_PREFIX = re.compile(r"^(?:\u20b9|rs\.?|inr)\s*", re.IGNORECASE)
_SUFFIX = re.compile(
    r"^(?P<number>[0-9][0-9,.\s]*?)\s*(?P<suffix>crores?|crs?|lakhs?|lacs?|thousands?|k)$",
    re.IGNORECASE,
)
_PLAIN_NUMBER = re.compile(r"^(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$")

_ONES = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


class MoneyError(ValueError):
    """Raised for an amount, format or text that cannot be interpreted."""


def group_digits(digits: str) -> str:
    """Group a run of ASCII digits the Indian way: ``1234567`` -> ``12,34,567``.

    Only the digits are grouped; the caller owns the sign, the decimal part and
    the currency symbol.
    """
    if not isinstance(digits, str):
        raise MoneyError(f"digits must be a string, got {type(digits).__name__}")
    if not digits or not digits.isdigit():
        raise MoneyError(f"expected a run of digits, got {digits!r}")
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.append(head[-2:])
        head = head[:-2]
    if head:
        groups.append(head)
    groups.reverse()
    groups.append(tail)
    return ",".join(groups)


def _to_decimal(value: object) -> Decimal:
    """Coerce an amount to a finite ``Decimal``, or raise ``MoneyError``."""
    if isinstance(value, bool):
        raise MoneyError("bool is not an amount")
    if isinstance(value, Decimal):
        amount = value
    elif isinstance(value, int):
        amount = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise MoneyError(f"amount must be finite, got {value!r}")
        amount = Decimal(str(value))
    elif isinstance(value, str):
        return parse_inr(value)
    else:
        raise MoneyError(
            f"unsupported amount type {type(value).__name__}; "
            "use int, str, Decimal or float"
        )
    if not amount.is_finite():
        raise MoneyError(f"amount must be finite, got {value!r}")
    return amount


def format_inr(
    amount: object,
    *,
    decimals: int = 2,
    symbol: str | None = DEFAULT_SYMBOL,
    space: bool = False,
    accounting: bool = False,
) -> str:
    """Format an amount with Indian digit grouping.

    Parameters
    ----------
    amount:
        ``int``, ``float``, ``Decimal``, or a string that :func:`parse_inr`
        understands.
    decimals:
        Digits after the point, ``0`` to :data:`MAX_DECIMALS`. Rounded
        half-up.
    symbol:
        Prefix to place before the number, ``'₹'`` by default. ``None`` or
        ``''`` formats bare digits.
    space:
        Put a space between the symbol and the number: ``'₹ 1,000.00'``.
    accounting:
        Render a negative amount as ``'(₹1,000.00)'`` instead of
        ``'-₹1,000.00'``.
    """
    if isinstance(decimals, bool) or not isinstance(decimals, int):
        raise MoneyError(f"decimals must be an int, got {type(decimals).__name__}")
    if not 0 <= decimals <= MAX_DECIMALS:
        raise MoneyError(f"decimals must be between 0 and {MAX_DECIMALS}, got {decimals}")
    if symbol is not None and not isinstance(symbol, str):
        raise MoneyError(f"symbol must be a string or None, got {type(symbol).__name__}")

    value = _to_decimal(amount).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    if value == 0:
        # Drop the sign of a rounded-away negative: '-₹0.00' is never wanted.
        value = value.copy_abs()

    negative = value < 0
    text = f"{abs(value):.{decimals}f}"
    whole, _, fraction = text.partition(".")
    body = group_digits(whole)
    if fraction:
        body = f"{body}.{fraction}"

    prefix = ""
    if symbol:
        prefix = f"{symbol} " if space else symbol
    rendered = prefix + body

    if not negative:
        return rendered
    if accounting:
        return f"({rendered})"
    return f"-{rendered}"


def _unwrap_parentheses(text: str) -> str:
    """Return the contents of ``( ... )``; raise on unbalanced brackets."""
    if text.startswith("(") and text.endswith(")"):
        inner = text[1:-1].strip()
        if not inner:
            raise MoneyError("empty amount inside parentheses")
        return inner
    if text.startswith("(") or text.endswith(")"):
        raise MoneyError(f"unbalanced parentheses in {text!r}")
    return text


def _strip_symbol(text: str) -> str:
    return _PREFIX.sub("", text).strip()


def parse_inr(text: object) -> Decimal:
    """Read an Indian amount from text: ``'\u20b9 1,23,456.78'`` -> ``Decimal('123456.78')``.

    Understood, in any combination:

    * a currency prefix — ``\u20b9``, ``Rs``, ``Rs.``, ``INR`` (any case),
      before or after a sign: ``-\u20b91,234.50`` reads as ``-1234.50``;
    * a currency suffix as a multiplier — ``2.5 lakh``, ``1.2 crore``,
      ``12k``, ``3 lacs``, ``5 thousand``;
    * Indian or Western grouping — ``1,23,456`` and ``123,456`` both work;
    * a leading or trailing sign, and parentheses for a negative:
      ``(1,234.50)`` is ``-1234.50``, and a sign outside the parentheses
      flips them: ``-(1,234.50)`` is ``1234.50``.

    Commas and spaces between digits are separators and are dropped, so
    ``'1 23 456'`` reads as ``123456``. Anything that is not a number after
    that raises ``MoneyError`` — this function never guesses.
    """
    if isinstance(text, Decimal):
        if not text.is_finite():
            raise MoneyError(f"amount must be finite, got {text!r}")
        return text
    if isinstance(text, bool):
        raise MoneyError("bool is not an amount")
    if isinstance(text, int):
        return Decimal(text)
    if isinstance(text, float):
        return _to_decimal(text)
    if not isinstance(text, str):
        raise MoneyError(
            f"unsupported amount type {type(text).__name__}; use int, str, Decimal or float"
        )

    working = text.replace("\u00a0", " ").replace("\u2009", " ").strip()
    if not working:
        raise MoneyError("empty amount")

    negative = False
    working = _strip_symbol(working)

    # 1. Parentheses around the whole amount mean "negative".
    if working.startswith("("):
        working = _unwrap_parentheses(working)
        negative = True
        working = _strip_symbol(working)

    # 2. A sign, which may itself precede the currency symbol.
    if working[:1] in ("+", "-"):
        if working[0] == "-":
            negative = not negative
        working = _strip_symbol(working[1:].strip())

    # 3. Parentheses again, for the "-(₹1,234)" shape.
    if working.startswith("(") or working.endswith(")"):
        working = _unwrap_parentheses(working)
        negative = not negative
        working = _strip_symbol(working)

    if working[:1] in ("+", "-"):
        raise MoneyError(f"not a valid amount: {text!r}")

    # 4. A multiplier suffix, e.g. "2.5 lakh".
    multiplier = Decimal(1)
    match = _SUFFIX.match(working)
    if match:
        suffix = match.group("suffix").lower()
        for name, factor in _MULTIPLIERS:
            if suffix == name:
                multiplier = factor
                break
        working = match.group("number").strip()

    # 5. Separators are noise; whatever is left must be a plain number.
    digits = working.replace(",", "").replace(" ", "")
    if not digits:
        raise MoneyError(f"no amount found in {text!r}")
    if not _PLAIN_NUMBER.match(digits):
        raise MoneyError(f"not a valid amount: {text!r}")

    try:
        amount = Decimal(digits)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise MoneyError(f"not a valid amount: {text!r}") from exc

    amount *= multiplier
    if multiplier != 1 and amount == amount.to_integral_value():
        # "2.5 lakh" is 250000, not 250000.0: drop the multiplier's scale.
        amount = amount.to_integral_value()
    return -amount if negative else amount


def _two_digit_words(number: int) -> str:
    if number < 20:
        return _ONES[number]
    tens, ones = divmod(number, 10)
    return _TENS[tens] if not ones else f"{_TENS[tens]}-{_ONES[ones]}"


def _three_digit_words(number: int) -> str:
    hundreds, rest = divmod(number, 100)
    parts = []
    if hundreds:
        parts.append(f"{_ONES[hundreds]} Hundred")
    if rest:
        parts.append(_two_digit_words(rest))
    return " ".join(parts)


def _integer_words(number: int) -> str:
    if number >= 10**7:
        high, rest = divmod(number, 10**7)
        words = f"{_integer_words(high)} Crore"
        return words if not rest else f"{words} {_integer_words(rest)}"

    parts = []
    for divisor, name in ((10**5, "Lakh"), (10**3, "Thousand")):
        count, number = divmod(number, divisor)
        if count:
            parts.append(f"{_integer_words(count)} {name}")
    if number:
        parts.append(_three_digit_words(number))
    return " ".join(parts)


def integer_to_words(number: int) -> str:
    """Spell a non-negative integer in the Indian system: ``1234567`` ->
    ``'Twelve Lakh Thirty-Four Thousand Five Hundred Sixty-Seven'``."""
    if isinstance(number, bool) or not isinstance(number, int):
        raise MoneyError(f"expected an int, got {type(number).__name__}")
    if number < 0:
        raise MoneyError(f"expected a non-negative integer, got {number}")
    if number == 0:
        return "Zero"
    return _integer_words(number)


def amount_to_words(
    amount: object,
    *,
    rupees: str = "Rupee",
    rupees_plural: str | None = None,
    paise: str = "Paise",
    paise_plural: str | None = None,
    include_paise: bool = True,
    include_only: bool = True,
) -> str:
    """Spell an amount the way a cheque or payment advice needs it.

    ``amount_to_words(1234.5)`` ->
    ``'One Thousand Two Hundred Thirty-Four Rupees and Fifty Paise Only'``.

    The amount is rounded to paise half-up first, so ``0.005`` becomes one
    paise rather than being silently dropped. ``rupees`` is the singular noun
    and its plural is formed by appending ``s`` (override with
    ``rupees_plural``). ``paise`` is invariant by default — ``'Fifty Paise'``,
    not ``'Fifty Paises'`` — and ``paise_plural`` overrides that. A negative
    amount is prefixed with ``'Minus '``.
    """
    for name, value in (("rupees", rupees), ("paise", paise)):
        if not isinstance(value, str) or not value.strip():
            raise MoneyError(f"{name} must be a non-empty string")
    for name, value in (("rupees_plural", rupees_plural), ("paise_plural", paise_plural)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise MoneyError(f"{name} must be a non-empty string or None")

    nouns = {
        "rupees": rupees,
        "rupees_plural": rupees + "s" if rupees_plural is None else rupees_plural,
        "paise": paise,
        "paise_plural": paise if paise_plural is None else paise_plural,
    }

    value = _to_decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    negative = value < 0
    value = abs(value)

    whole = int(value)
    fraction = int((value - whole) * 100)

    def noun(count: int, singular: str, plural: str) -> str:
        return singular if count == 1 else plural

    words = f"{integer_to_words(whole)} {noun(whole, nouns['rupees'], nouns['rupees_plural'])}"
    if include_paise and fraction:
        paise_words = noun(fraction, nouns["paise"], nouns["paise_plural"])
        words = f"{words} and {integer_to_words(fraction)} {paise_words}"
    if include_only:
        words = f"{words} Only"
    return f"Minus {words}" if negative else words
