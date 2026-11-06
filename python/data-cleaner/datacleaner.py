"""datacleaner -- a small, dependency-free toolkit for cleaning tabular records.

The toolkit works on *records*: plain ``dict`` objects that map field names to
values, as you would get from ``csv.DictReader`` or a JSON API. Every helper is
pure (it never mutates its input) and returns a new value, so a cleaning
pipeline is easy to reason about and to test.

Public API
----------
- :func:`normalize_whitespace`
- :func:`is_missing`
- :func:`to_number`
- :func:`normalize_phone`
- :func:`normalize_email`
- :func:`clean_record`
- :func:`clean_records`
- :func:`dedupe_records`
- :func:`missing_required`

Run the tests with::

    cd python/data-cleaner
    python -m pytest

Trust boundary
--------------
Every helper here is meant to run over *untrusted* input -- a CSV export, a
spreadsheet a colleague mailed over, a JSON payload from a third party -- so
the rules are deliberately conservative:

- nothing is coerced silently: a value that is not recognisably a number, an
  email or a phone number becomes ``None`` instead of a best-effort guess, so
  bad data surfaces in the cleaned output rather than as a plausible-looking
  wrong value;
- no helper executes, evaluates, imports or shells out on the data, and none
  touches the network or the filesystem;
- no helper mutates its input; every one returns a new value;
- the only exception any helper raises is for a bad *argument* (a malformed
  ``country_code``, a non-callable transformer), never for bad *data*.

Known limitations
-----------------
- :func:`normalize_phone` implements the Indian numbering plan only: a 10-digit
  national number, an optional national trunk ``0``, and an optional
  ``country_code`` prefix. Plans that keep a significant leading zero in the
  national number (Italy, for one) are not supported, because the trunk ``0``
  and a significant ``0`` are indistinguishable without a full numbering-plan
  database.
- :func:`normalize_email` is a shape check, not an RFC 5322 parser: it verifies
  a single ``@`` and a dotted domain, nothing more.
- :func:`dedupe_records` holds a frozen copy of every distinct record it has
  seen, so its memory use is O(number of distinct records).
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "DEFAULT_MISSING_TOKENS",
    "normalize_whitespace",
    "is_missing",
    "to_number",
    "normalize_phone",
    "normalize_email",
    "clean_record",
    "clean_records",
    "dedupe_records",
    "missing_required",
]

#: String values (compared case-insensitively, after whitespace normalisation)
#: that are treated as "no value" / null.
DEFAULT_MISSING_TOKENS = frozenset(
    {"", "na", "n/a", "null", "none", "nil", "nan", "-", "--", "unknown"}
)

_WHITESPACE_RE = re.compile(r"\s+")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_NON_DIGIT_RE = re.compile(r"\D")
#: Currency symbols and thousands separators removed before numeric coercion.
#: Only applied *after* :data:`_NUMBER_GRAMMAR_RE` has accepted the string, so a
#: symbol can never be stripped from the middle of a number.
_NUMBER_NOISE_RE = re.compile(r"[,\s\u20b9$€£]")
#: The shapes :func:`to_number` is willing to coerce, checked before ``float()``.
#: A leading sign, then an optional leading currency symbol, then either a
#: grouped integer (``1,234`` / ``1 234``) or a plain decimal (``12``, ``12.5``,
#: ``.5``), then an optional exponent. Anything else -- ``12$34``, ``1,2,3``,
#: ``1_000`` -- is rejected rather than quietly turned into a different number.
_NUMBER_GRAMMAR_RE = re.compile(
    r"^\s*[+-]?(?:[\u20b9$€£]\s?)?"
    r"(?:\d{1,3}(?:[,\s]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)"
    r"(?:[eE][+-]?\d+)?\s*$"
)
#: An ITU-T E.164 country calling code is one to three digits.
_COUNTRY_CODE_RE = re.compile(r"^\d{1,3}$")


def normalize_whitespace(value: Any) -> Any:
    """Trim a string and collapse internal runs of whitespace to a single space.

    Non-string values are returned unchanged, which makes this safe to apply
    across a heterogeneous record.
    """
    if not isinstance(value, str):
        return value
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalized_tokens(missing_tokens: Iterable[str]) -> frozenset[str]:
    """Return *missing_tokens* whitespace-normalised and casefolded.

    The default set is already normalised, so it is returned as-is on the hot
    path. A caller-supplied set is normalised the same way the value is, which
    is what makes ``missing_tokens={"N/A"}`` match the value ``"n/a"``; without
    it the comparison would be case-sensitive in one direction only and the
    caller's tokens would silently never match. Non-string tokens are dropped:
    only a string value can ever be compared against them.
    """
    if missing_tokens is DEFAULT_MISSING_TOKENS:
        return DEFAULT_MISSING_TOKENS
    return frozenset(
        normalize_whitespace(token).casefold()
        for token in missing_tokens
        if isinstance(token, str)
    )


def is_missing(
    value: Any, missing_tokens: Iterable[str] = DEFAULT_MISSING_TOKENS
) -> bool:
    """Return ``True`` if *value* should be treated as absent.

    ``None`` is always missing, and so is a non-finite float: ``to_number`` maps
    ``nan``/``inf`` to ``None``, so treating them as present here would make
    :func:`clean_record` and :func:`missing_required` disagree about the same
    cell. Strings are whitespace-normalised and compared case-insensitively
    against *missing_tokens* (so ``"  N/A "`` counts as missing, and a custom
    ``{"N/A"}`` matches ``"n/a"``). Any other type is considered present.
    """
    if value is None:
        return True
    if isinstance(value, float) and not math.isfinite(value):
        return True
    if isinstance(value, str):
        return normalize_whitespace(value).casefold() in _normalized_tokens(
            missing_tokens
        )
    return False


def to_number(value: Any) -> int | float | None:
    """Coerce *value* to a finite ``int`` or ``float``, or ``None`` if it is not.

    Handles plain numbers, numeric strings with thousands separators or a
    leading currency symbol (``"1,234"`` -> ``1234``, ``"\u20b91,200"`` -> ``1200``),
    and integral floats (``"12.0"`` -> ``12``).

    Non-finite values are rejected rather than passed through: ``float("nan")``,
    ``float("inf")`` and their string spellings (``"nan"``, ``"inf"``,
    ``"-infinity"``, or an overflow such as ``"1e400"``) all return ``None``.
    A ``nan``/``inf`` that reaches a downstream sum or average silently poisons
    the whole aggregate, so "not finite" is treated as "not a number" here.

    Booleans are deliberately rejected because ``True == 1`` in Python and
    coercing them is almost never what a data pipeline wants.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        # ``int`` is always finite; guard the ``float`` case for nan/inf.
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        # Validate the *whole* shape first. Stripping separators from an
        # arbitrary string would turn corrupt cells into different numbers --
        # "12$34" into 1234, "1,2,3" into 123 -- and a pipeline has no way to
        # tell that from a genuine value.
        if not _NUMBER_GRAMMAR_RE.match(value):
            return None
        cleaned = _NUMBER_NOISE_RE.sub("", value)
        if not cleaned:
            return None
        try:
            as_float = float(cleaned)
        except ValueError:
            return None
        if not math.isfinite(as_float):
            return None
        if as_float.is_integer():
            return int(as_float)
        return as_float
    return None


def normalize_phone(value: Any, country_code: str = "91") -> str | None:
    """Normalise a phone number to ``+<country_code><national_number>`` form.

    Accepts strings or integers and tolerates spaces, dashes, parentheses and a
    leading ``+`` or national trunk ``0``. The national number is expected to be
    10 digits (the common length in India); anything else returns ``None`` so
    bad data is surfaced rather than silently mangled.

    ``country_code`` is accepted as ``"91"``, ``"+91"`` or the integer ``91``
    and must hold one to three digits (the E.164 range); anything else raises
    :class:`ValueError`. That is a malformed
    *argument* rather than bad data, and letting it through used to emit an
    invalid number such as ``+9999999876543210`` -- a config typo that would
    quietly corrupt every phone number in the batch instead of failing once.

    Floats are rejected even when integral (``9876543210.0``), because a float
    in a phone column means the value has already been through arithmetic.
    """
    cc = str(country_code).strip()
    if cc.startswith("+"):
        cc = cc[1:]
    if not _COUNTRY_CODE_RE.match(cc):
        # Reject rather than strip: silently turning "91x" into "91" would
        # accept a typo the caller never meant.
        raise ValueError(f"country_code must be 1-3 digits, got {country_code!r}")

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        digits = str(value)
    elif isinstance(value, str):
        digits = _NON_DIGIT_RE.sub("", value)
    else:
        return None

    digits = digits.lstrip("0")  # drop a national trunk prefix
    if not digits:
        return None

    if digits.startswith(cc) and len(digits) == len(cc) + 10:
        national = digits[len(cc):]
    elif len(digits) == 10:
        national = digits
    else:
        return None

    return f"+{cc}{national}"


def normalize_email(value: Any) -> str | None:
    """Return a lower-cased, trimmed email address, or ``None`` if invalid.

    This is a deliberately loose check (single ``@`` with a dotted domain) --
    enough to catch obvious junk without rejecting valid addresses.
    """
    if not isinstance(value, str):
        return None
    candidate = normalize_whitespace(value).casefold()
    if not candidate or not _EMAIL_RE.match(candidate):
        return None
    return candidate


def clean_record(
    record: Mapping[str, Any],
    *,
    missing_tokens: Iterable[str] = DEFAULT_MISSING_TOKENS,
    transformers: Mapping[str, Callable[[Any], Any]] | None = None,
) -> dict[str, Any]:
    """Return a cleaned copy of *record*.

    Every value is whitespace-normalised; values that match *missing_tokens*
    become ``None``. A per-field ``transformers`` mapping is then applied to
    non-missing values (for example ``{"phone": normalize_phone}``). The input
    mapping is never mutated.

    An exception raised by a transformer propagates: a cleaning pipeline should
    stop on a broken transformer rather than write half-cleaned records, and the
    traceback names the field. A non-callable entry raises :class:`TypeError` up
    front, with the field name, instead of a ``TypeError`` from deep inside the
    loop. Transformer keys that the record does not contain are ignored.
    """
    transformers = transformers or {}
    for field, transformer in transformers.items():
        if not callable(transformer):
            raise TypeError(
                f"transformer for {field!r} is not callable: {transformer!r}"
            )
    cleaned: dict[str, Any] = {}
    for field, raw in record.items():
        value = normalize_whitespace(raw)
        if is_missing(value, missing_tokens):
            value = None
        elif field in transformers:
            value = transformers[field](value)
        cleaned[field] = value
    return cleaned


def clean_records(
    records: Iterable[Mapping[str, Any]],
    *,
    missing_tokens: Iterable[str] = DEFAULT_MISSING_TOKENS,
    transformers: Mapping[str, Callable[[Any], Any]] | None = None,
    drop_empty: bool = True,
) -> list[dict[str, Any]]:
    """Clean a sequence of records.

    With ``drop_empty`` (the default), records whose every field is missing are
    discarded. Order is preserved.
    """
    out: list[dict[str, Any]] = []
    for record in records:
        cleaned = clean_record(
            record, missing_tokens=missing_tokens, transformers=transformers
        )
        if drop_empty and all(value is None for value in cleaned.values()):
            continue
        out.append(cleaned)
    return out


def _freeze(value: Any) -> Any:
    """Return a hashable, order-stable stand-in for *value* so it can go in a set."""
    if isinstance(value, Mapping):
        # Sort by ``str(key)``: two records holding the same nested mapping must
        # freeze identically regardless of insertion order, and sorting by the
        # raw key would raise ``TypeError`` on a mapping with mixed key types.
        return tuple(
            sorted(
                ((k, _freeze(v)) for k, v in value.items()),
                key=lambda kv: str(kv[0]),
            )
        )
    if isinstance(value, (set, frozenset)):
        # A set has no meaningful order, so freeze it order-independently: two
        # equal sets must compare equal after freezing, or ``dedupe_records``
        # keeps both copies of a record that differs only in set iteration
        # order. Sort on ``repr`` because the elements themselves need not be
        # mutually comparable (``{1, "a"}``).
        return tuple(sorted((_freeze(v) for v in value), key=repr))
    if isinstance(value, (list, tuple)):
        # Sequences are ordered by definition, so their order is preserved.
        return tuple(_freeze(v) for v in value)
    return value


def _record_key(record: Mapping[str, Any], key_fields: Sequence[str] | None) -> tuple:
    if key_fields is None:
        # ``sorted(record.items())`` raises on mixed key types; sorting by
        # ``str(key)`` keeps the order stable without that failure mode.
        items = sorted(record.items(), key=lambda kv: str(kv[0]))
    else:
        items = [(field, record.get(field)) for field in key_fields]
    return tuple((field, _freeze(value)) for field, value in items)


def dedupe_records(
    records: Iterable[Mapping[str, Any]],
    *,
    key_fields: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Drop duplicate records, keeping the first occurrence of each.

    Comparison uses *key_fields* when given, otherwise the whole record. Works
    with unhashable values (lists/dicts/sets) by freezing them for the
    comparison; a set compares by membership, not by iteration order.

    The returned records are shallow copies. Memory use is O(number of distinct
    records): every key seen so far is retained.
    """
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for record in records:
        key = _record_key(record, key_fields)
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(record))
    return out


def missing_required(
    record: Mapping[str, Any],
    required: Sequence[str],
    *,
    missing_tokens: Iterable[str] = DEFAULT_MISSING_TOKENS,
) -> list[str]:
    """Return the *required* fields that are absent or missing in *record*.

    A field counts as missing when it is absent from the mapping, or when its
    value matches *missing_tokens* -- the same tokens :func:`clean_record` uses
    by default -- so the two helpers agree on what "empty" means.
    """
    return [field for field in required if is_missing(record.get(field), missing_tokens)]
