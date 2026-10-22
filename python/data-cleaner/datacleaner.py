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
#: Currency symbols and thousands separators stripped before numeric coercion.
_NUMBER_NOISE_RE = re.compile(r"[,\s\u20b9$€£]")


def normalize_whitespace(value: Any) -> Any:
    """Trim a string and collapse internal runs of whitespace to a single space.

    Non-string values are returned unchanged, which makes this safe to apply
    across a heterogeneous record.
    """
    if not isinstance(value, str):
        return value
    return _WHITESPACE_RE.sub(" ", value).strip()


def is_missing(
    value: Any, missing_tokens: Iterable[str] = DEFAULT_MISSING_TOKENS
) -> bool:
    """Return ``True`` if *value* should be treated as absent.

    ``None`` is always missing. Strings are whitespace-normalised and compared
    case-insensitively against *missing_tokens* (so ``"  N/A "`` counts as
    missing). Any other type is considered present.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return normalize_whitespace(value).casefold() in missing_tokens
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
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        digits = str(value)
    elif isinstance(value, str):
        digits = _NON_DIGIT_RE.sub("", value)
    else:
        return None

    cc = _NON_DIGIT_RE.sub("", str(country_code))
    if not cc:
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
    """
    transformers = transformers or {}
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
    if isinstance(value, (list, tuple, set, frozenset)):
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
    with unhashable values (lists/dicts) by freezing them for the comparison.
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
