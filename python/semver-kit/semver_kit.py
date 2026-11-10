"""Semantic Versioning 2.0.0: parse, order, bump, and match npm-style ranges.

Standard library only.

    >>> v = Version.parse("1.2.3-rc.1+build.5")
    >>> v.release, v.prerelease
    ((1, 2, 3), ('rc', 1))

    >>> satisfies("1.4.9", "^1.2.3")
    True

Precedence follows the spec exactly, including the parts people get wrong:
build metadata is ignored, numeric prerelease identifiers compare numerically
(``beta.2 < beta.11``), and a version with a prerelease sorts *below* its
release.

Every rejection raises :class:`SemVerError`, a ``ValueError`` subclass, so one
``except`` clause covers malformed input from any entry point. That holds for
hostile input too: numeric components are capped at
``_MAX_NUMERIC_DIGITS`` digits so that a 5000-digit "version" cannot turn a
parse into a CPU burn or escape as a bare ``ValueError`` from ``int()``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Iterable, Optional, Sequence, Tuple

__all__ = [
    "SemVerError",
    "Version",
    "Comparator",
    "ComparatorSet",
    "Range",
    "parse",
    "parse_range",
    "satisfies",
    "sort_versions",
    "max_satisfying",
    "min_satisfying",
]


class SemVerError(ValueError):
    """Raised for anything that is not a valid version or range."""


# --------------------------------------------------------------------------
# Lexical building blocks
# --------------------------------------------------------------------------

# ASCII only, deliberately: ``str.isdigit()`` is true for "١٢٣" and
# ``int()`` happily turns that into 123. Versions are ASCII.
_NUMERIC = re.compile(r"[0-9]+")
_IDENT = re.compile(r"[0-9A-Za-z-]+")
_WILDCARDS = ("*", "x", "X")

# Guard rail for untrusted input. CPython 3.11+ makes ``int()`` refuse a string
# longer than ``sys.get_int_max_str_digits()`` (4300 by default) and older
# builds spend quadratic time on one, so a hostile version string could either
# burn CPU or raise a bare ``ValueError`` straight past the ``SemVerError``
# contract this module documents. No real version component is remotely this
# long, so the cap costs nothing and keeps every rejection on one exception
# type. Build identifiers are *not* capped: they are never converted to ``int``.
_MAX_NUMERIC_DIGITS = 64


def _to_int(text: str) -> int:
    """``int()`` for an untrusted numeric identifier, bounded and typed.

    Every conversion of attacker-controlled digits goes through here so that
    an over-long component surfaces as :class:`SemVerError` rather than as the
    ``ValueError`` ``int()`` itself raises when ``sys.get_int_max_str_digits()``
    is exceeded (or, on interpreters without that limit, as a CPU burn).
    """
    if len(text) > _MAX_NUMERIC_DIGITS:
        raise SemVerError(
            f"numeric identifier has {len(text)} digits, limit is {_MAX_NUMERIC_DIGITS}"
        )
    try:
        return int(text)
    except ValueError:  # pragma: no cover - the length check above gets there first
        # Only reachable if the embedding process lowered
        # ``sys.set_int_max_str_digits()`` below our own cap.
        raise SemVerError(f"invalid numeric identifier {text!r}") from None


def _split_identifiers(text: str, label: str) -> Tuple[str, ...]:
    """Split a dot-separated identifier list, enforcing the spec's rules."""
    if not text:
        raise SemVerError(f"empty {label} in version")
    parts = text.split(".")
    for part in parts:
        if not _IDENT.fullmatch(part):
            raise SemVerError(f"invalid {label} identifier {part!r}")
        if label == "prerelease" and _NUMERIC.fullmatch(part) and len(part) > 1 and part[0] == "0":
            raise SemVerError(f"numeric {label} identifier {part!r} has a leading zero")
        if label == "prerelease" and len(part) > _MAX_NUMERIC_DIGITS and _NUMERIC.fullmatch(part):
            # Rejected at construction rather than at first comparison: a
            # ``Version`` carrying this identifier would otherwise parse fine
            # and then blow up inside ``precedence_key()`` on any sort.
            raise SemVerError(
                f"numeric prerelease identifier has {len(part)} digits, "
                f"limit is {_MAX_NUMERIC_DIGITS}"
            )
    return tuple(parts)


# --------------------------------------------------------------------------
# Version
# --------------------------------------------------------------------------


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    """A semantic version.

    Ordering and equality use *precedence*, which by the spec ignores build
    metadata: ``Version(1, 2, 3, build=("a",)) == Version(1, 2, 3)``. Use
    :meth:`identical` when you need to distinguish them.
    """

    major: int
    minor: int
    patch: int
    prerelease: Tuple[str, ...] = ()
    build: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("major", "minor", "patch"):
            value = getattr(self, name)
            # bool is an int subclass; True is not version 1.
            if isinstance(value, bool) or not isinstance(value, int):
                raise SemVerError(f"{name} must be an int, got {value!r}")
            if value < 0:
                raise SemVerError(f"{name} must be non-negative, got {value}")

        for name in ("prerelease", "build"):
            value = getattr(self, name)
            if isinstance(value, str):
                value = _split_identifiers(value, name)
            else:
                value = tuple(value)
                for part in value:
                    if not isinstance(part, str):
                        raise SemVerError(f"{name} identifiers must be strings, got {part!r}")
                if value:
                    value = _split_identifiers(".".join(value), name)
            object.__setattr__(self, name, value)

    # -- construction ------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> "Version":
        """Parse a strict ``MAJOR.MINOR.PATCH[-pre][+build]`` string.

        Strict means exactly three numeric parts, no ``v`` prefix, no ``=``,
        no leading zeros. Whitespace around the whole string is tolerated.

        Each of the three numeric parts is limited to
        ``_MAX_NUMERIC_DIGITS`` digits; anything longer raises
        :class:`SemVerError` instead of reaching ``int()``.
        """
        if not isinstance(text, str):
            raise SemVerError(f"version must be a string, got {type(text).__name__}")
        raw = text
        text = text.strip()
        if not text:
            raise SemVerError("version is empty")
        if text[0] in "vV=":
            raise SemVerError(f"invalid version {raw!r}: no 'v' or '=' prefix in a strict version")

        build: Tuple[str, ...] = ()
        if "+" in text:
            text, _, build_text = text.partition("+")
            build = _split_identifiers(build_text, "build")

        prerelease: Tuple[str, ...] = ()
        if "-" in text:
            text, _, pre_text = text.partition("-")
            prerelease = _split_identifiers(pre_text, "prerelease")

        parts = text.split(".")
        if len(parts) != 3:
            raise SemVerError(f"invalid version {raw!r}: expected MAJOR.MINOR.PATCH")

        numbers = []
        for part in parts:
            if not _NUMERIC.fullmatch(part):
                raise SemVerError(f"invalid version {raw!r}: {part!r} is not a number")
            if len(part) > 1 and part[0] == "0":
                raise SemVerError(f"invalid version {raw!r}: {part!r} has a leading zero")
            numbers.append(_to_int(part))

        return cls(numbers[0], numbers[1], numbers[2], prerelease, build)

    # -- properties --------------------------------------------------------

    @property
    def release(self) -> Tuple[int, int, int]:
        """The ``major, minor, patch`` triple, without prerelease or build."""
        return (self.major, self.minor, self.patch)

    @property
    def is_prerelease(self) -> bool:
        return bool(self.prerelease)

    @property
    def base(self) -> str:
        """``"1.2.3"`` — the release triple as text."""
        return "%d.%d.%d" % self.release

    # -- ordering ----------------------------------------------------------

    def precedence_key(self):
        """Sort key implementing the spec's precedence rules."""
        # Numeric identifiers sort below alphanumeric ones (the leading 0/1
        # tag), and numerically rather than lexically, so ``beta.2 < beta.11``.
        # ``_to_int`` cannot raise here: ``__post_init__`` already rejected an
        # over-long numeric prerelease identifier.
        identifiers = tuple(
            (0, _to_int(part)) if _NUMERIC.fullmatch(part) else (1, part)
            for part in self.prerelease
        )
        # A release outranks any prerelease of the same triple, and a longer
        # prerelease list outranks a prefix of itself (alpha < alpha.1) —
        # Python's tuple comparison gives both for free.
        return (self.major, self.minor, self.patch, 0 if self.prerelease else 1, identifiers)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence_key() == other.precedence_key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        return self.precedence_key() < other.precedence_key()

    def __hash__(self) -> int:
        return hash(self.precedence_key())

    def identical(self, other: "Version") -> bool:
        """True only if build metadata matches too."""
        return (
            isinstance(other, Version)
            and self.release == other.release
            and self.prerelease == other.prerelease
            and self.build == other.build
        )

    # -- bumping -----------------------------------------------------------

    def bump_major(self) -> "Version":
        """1.4.2 -> 2.0.0. A prerelease of ``X.0.0`` releases as ``X.0.0``."""
        if self.minor == 0 and self.patch == 0 and self.prerelease:
            return Version(self.major, 0, 0)
        return Version(self.major + 1, 0, 0)

    def bump_minor(self) -> "Version":
        """1.4.2 -> 1.5.0. A prerelease of ``X.Y.0`` releases as ``X.Y.0``."""
        if self.patch == 0 and self.prerelease:
            return Version(self.major, self.minor, 0)
        return Version(self.major, self.minor + 1, 0)

    def bump_patch(self) -> "Version":
        """1.4.2 -> 1.4.3. A prerelease releases as its own triple."""
        if self.prerelease:
            return Version(self.major, self.minor, self.patch)
        return Version(self.major, self.minor, self.patch + 1)

    def bump_prerelease(self, identifier: Optional[str] = None) -> "Version":
        """Increment the last numeric prerelease identifier.

        ``1.4.2`` -> ``1.4.3-0``; ``1.4.3-0`` -> ``1.4.3-1``; ``1.4.3-beta``
        -> ``1.4.3-beta.0``. With an ``identifier``, restart that channel:
        ``1.4.3-rc.1`` with ``"beta"`` -> ``1.4.3-beta.0``.
        """
        if identifier is not None:
            if not isinstance(identifier, str) or not _IDENT.fullmatch(identifier):
                raise SemVerError(f"invalid prerelease identifier {identifier!r}")
            if _NUMERIC.fullmatch(identifier) and len(identifier) > 1 and identifier[0] == "0":
                raise SemVerError(f"numeric prerelease identifier {identifier!r} has a leading zero")
            if self.prerelease and self.prerelease[0] == identifier:
                rest = self.prerelease[1:]
                for index in range(len(rest) - 1, -1, -1):
                    if _NUMERIC.fullmatch(rest[index]):
                        tail = rest[:index] + (str(_to_int(rest[index]) + 1),)
                        return Version(*self.release, (identifier,) + tail)
                return Version(*self.release, (identifier,) + rest + ("0",))
            return Version(*self.release, (identifier, "0"))

        if not self.prerelease:
            return Version(self.major, self.minor, self.patch + 1, ("0",))
        parts = list(self.prerelease)
        for index in range(len(parts) - 1, -1, -1):
            if _NUMERIC.fullmatch(parts[index]):
                parts[index] = str(_to_int(parts[index]) + 1)
                return Version(*self.release, tuple(parts))
        return Version(*self.release, tuple(parts) + ("0",))

    # -- rendering ---------------------------------------------------------

    def __str__(self) -> str:
        text = self.base
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def __repr__(self) -> str:
        return f"Version({str(self)!r})"


def parse(text: str) -> Version:
    """Shorthand for :meth:`Version.parse`."""
    return Version.parse(text)


# --------------------------------------------------------------------------
# Ranges
# --------------------------------------------------------------------------

_OPERATORS = ("~>", ">=", "<=", "^", "~", ">", "<", "=")
_COMPARATOR_OPS = ("=", "<", "<=", ">", ">=")
_OPERATOR_SPACE = re.compile(r"(<=|>=|~>|\^|~|<|>|=)\s+")


@dataclass(frozen=True)
class Comparator:
    """One ``op version`` primitive. ``=`` means exact precedence match."""

    op: str
    version: Version

    def __post_init__(self) -> None:
        if self.op not in _COMPARATOR_OPS:
            raise SemVerError(f"unknown comparator operator {self.op!r}")
        if not isinstance(self.version, Version):
            raise SemVerError("comparator needs a Version")

    def test(self, version: Version) -> bool:
        # Compare precedence keys, not ``Version`` objects: ``<`` on the
        # dataclass would honour build metadata through ``identical()``
        # semantics, while a comparator must ignore it.
        left, right = version.precedence_key(), self.version.precedence_key()
        if self.op == "=":
            return left == right
        if self.op == "<":
            return left < right
        if self.op == "<=":
            return left <= right
        if self.op == ">":
            return left > right
        return left >= right

    def __str__(self) -> str:
        return f"{self.op}{self.version}"


@dataclass(frozen=True)
class ComparatorSet:
    """A conjunction of comparators. An empty set means "any version"."""

    comparators: Tuple[Comparator, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparators", tuple(self.comparators))

    @property
    def is_any(self) -> bool:
        return not self.comparators

    def matches(self, version: Version, *, include_prerelease: bool = False) -> bool:
        if not isinstance(version, Version):
            raise SemVerError(f"expected a Version, got {type(version).__name__}")
        if not all(c.test(version) for c in self.comparators):
            return False
        if version.prerelease and not include_prerelease:
            # A prerelease is only in range if the range itself opted into
            # prereleases at that exact triple: ^1.2.3-beta.1 matches
            # 1.2.3-beta.5 but never 1.3.0-rc.1 or 1.2.4-alpha.
            return any(
                c.version.prerelease and c.version.release == version.release
                for c in self.comparators
            )
        return True

    def __str__(self) -> str:
        return " ".join(str(c) for c in self.comparators) or "*"


@dataclass(frozen=True)
class Range:
    """A disjunction of :class:`ComparatorSet` — what ``||`` separates."""

    sets: Tuple[ComparatorSet, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "sets", tuple(self.sets))
        if not self.sets:
            raise SemVerError("a range needs at least one comparator set")

    @classmethod
    def parse(cls, text: "str | Range") -> "Range":
        """Parse an npm-style range, or pass a :class:`Range` straight through.

        ``""``, ``"*"`` and ``"any"`` all mean "matches everything"; an empty
        alternative such as ``"1.2.3 ||"`` is a typo and raises rather than
        silently widening the range to all versions.
        """
        if isinstance(text, Range):
            return text
        if not isinstance(text, str):
            raise SemVerError(f"range must be a string, got {type(text).__name__}")
        chunks = text.split("||")
        if len(chunks) > 1 and any(not chunk.strip() for chunk in chunks):
            # "1.2.3 ||" is a typo, not a range that matches everything.
            raise SemVerError(f"empty alternative in range {text!r}")
        sets = tuple(_parse_comparator_set(chunk) for chunk in chunks)
        return cls(sets)

    def matches(self, version, *, include_prerelease: bool = False) -> bool:
        if isinstance(version, str):
            version = Version.parse(version)
        return any(s.matches(version, include_prerelease=include_prerelease) for s in self.sets)

    def __str__(self) -> str:
        return " || ".join(str(s) for s in self.sets)

    def __repr__(self) -> str:
        return f"Range({str(self)!r})"


def _version(major: int, minor: int, patch: int, prerelease: Sequence[str] = ()) -> Version:
    return Version(major, minor, patch, tuple(prerelease))


def _parse_partial(text: str):
    """Parse a possibly-partial version into ``(parts, prerelease, build)``.

    ``parts`` is always a 3-tuple where ``None`` marks a wildcard position.
    """
    raw = text
    if not text:
        raise SemVerError("empty version in range")
    if text[0] in "vV" and len(text) > 1:
        text = text[1:]

    build: Tuple[str, ...] = ()
    if "+" in text:
        text, _, build_text = text.partition("+")
        build = _split_identifiers(build_text, "build")

    prerelease: Tuple[str, ...] = ()
    if "-" in text:
        text, _, pre_text = text.partition("-")
        prerelease = _split_identifiers(pre_text, "prerelease")

    parts: list = []
    wildcard_seen = False
    for part in text.split("."):
        if part in _WILDCARDS:
            wildcard_seen = True
            parts.append(None)
        elif part == "":
            raise SemVerError(f"invalid version {raw!r} in range")
        elif not _NUMERIC.fullmatch(part):
            raise SemVerError(f"invalid version {raw!r} in range: {part!r} is not a number")
        else:
            if wildcard_seen:
                raise SemVerError(f"invalid version {raw!r} in range: wildcard must come last")
            if len(part) > 1 and part[0] == "0":
                raise SemVerError(f"invalid version {raw!r} in range: {part!r} has a leading zero")
            parts.append(_to_int(part))

    if len(parts) > 3:
        raise SemVerError(f"invalid version {raw!r} in range: too many parts")
    while len(parts) < 3:
        parts.append(None)

    if prerelease and None in parts:
        raise SemVerError(f"invalid version {raw!r} in range: a prerelease needs major.minor.patch")

    return tuple(parts), prerelease, build


def _expand(operator: str, major, minor, patch, prerelease) -> Tuple[Comparator, ...]:
    """Turn one range token into the comparators that implement it.

    ``None`` marks a wildcard position, and an empty tuple means "no
    constraint" (``>=*`` matches anything). The ``x or 0`` idiom below relies
    on ``None`` and ``0`` collapsing to the same value — a real zero in a
    position that is also a legal wildcard cannot mean anything else.
    """

    def exact() -> Version:
        return _version(major, minor, patch, prerelease)

    if operator in ("", "="):
        if major is None:
            return ()
        if minor is None:
            return (Comparator(">=", _version(major, 0, 0)),
                    Comparator("<", _version(major + 1, 0, 0)))
        if patch is None:
            return (Comparator(">=", _version(major, minor, 0)),
                    Comparator("<", _version(major, minor + 1, 0)))
        return (Comparator("=", exact()),)

    if operator == ">=":
        if major is None:
            return ()
        return (Comparator(">=", _version(major, minor or 0, patch or 0, prerelease)),)

    if operator == ">":
        if major is None:
            return ()
        if minor is None:
            return (Comparator(">=", _version(major + 1, 0, 0)),)
        if patch is None:
            return (Comparator(">=", _version(major, minor + 1, 0)),)
        return (Comparator(">", exact()),)

    if operator == "<":
        if major is None:
            # "<*" cannot match anything sensible; npm treats it as empty.
            return (Comparator("<", _version(0, 0, 0)),)
        return (Comparator("<", _version(major, minor or 0, patch or 0, prerelease)),)

    if operator == "<=":
        if major is None:
            return ()
        if minor is None:
            return (Comparator("<", _version(major + 1, 0, 0)),)
        if patch is None:
            return (Comparator("<", _version(major, minor + 1, 0)),)
        return (Comparator("<=", exact()),)

    if operator in ("~", "~>"):
        if major is None:
            return ()
        if minor is None:
            return (Comparator(">=", _version(major, 0, 0)),
                    Comparator("<", _version(major + 1, 0, 0)))
        if patch is None:
            return (Comparator(">=", _version(major, minor, 0)),
                    Comparator("<", _version(major, minor + 1, 0)))
        return (Comparator(">=", exact()),
                Comparator("<", _version(major, minor + 1, 0)))

    if operator == "^":
        if major is None:
            return ()
        if minor is None:
            return (Comparator(">=", _version(major, 0, 0)),
                    Comparator("<", _version(major + 1, 0, 0)))
        if patch is None:
            if major == 0:
                return (Comparator(">=", _version(0, minor, 0)),
                        Comparator("<", _version(0, minor + 1, 0)))
            return (Comparator(">=", _version(major, minor, 0)),
                    Comparator("<", _version(major + 1, 0, 0)))
        # The caret pins the leftmost non-zero part: ^0.0.3 allows only 0.0.3,
        # ^0.2.3 allows 0.2.x, ^1.2.3 allows 1.x.
        if major > 0:
            upper = _version(major + 1, 0, 0)
        elif minor > 0:
            upper = _version(0, minor + 1, 0)
        else:
            upper = _version(0, 0, patch + 1)
        return (Comparator(">=", exact()), Comparator("<", upper))

    raise SemVerError(f"unknown range operator {operator!r}")


def _hyphen(low: str, high: str) -> Tuple[Comparator, ...]:
    """``1.2 - 2.3.4`` style: inclusive on both ends, wildcards fill in."""
    low_parts, low_pre, _ = _parse_partial(low)
    high_parts, high_pre, _ = _parse_partial(high)
    if low_parts[0] is None:
        raise SemVerError(f"invalid hyphen range start {low!r}")

    comparators = [Comparator(">=", _version(low_parts[0], low_parts[1] or 0, low_parts[2] or 0, low_pre))]

    major, minor, patch = high_parts
    if major is None:
        return tuple(comparators)
    if minor is None:
        comparators.append(Comparator("<", _version(major + 1, 0, 0)))
    elif patch is None:
        comparators.append(Comparator("<", _version(major, minor + 1, 0)))
    else:
        comparators.append(Comparator("<=", _version(major, minor, patch, high_pre)))
    return tuple(comparators)


def _parse_comparator_set(text: str) -> ComparatorSet:
    """Parse one ``||``-free chunk into the conjunction it describes."""
    chunk = text.strip()
    if chunk in ("", "*", "x", "X", "any", "latest"):
        return ComparatorSet(())

    tokens = _OPERATOR_SPACE.sub(r"\1", chunk).split()

    comparators: list = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        # Hyphen range: "<low> - <high>". The dash must stand alone, which is
        # what keeps "1.2.3-beta - 2.0.0" a hyphen range rather than a version
        # called "beta - 2.0.0". ``index + 2 < len(tokens)`` is the guard that
        # both operands exist, so a trailing "1.2.3 -" falls through to the
        # explicit error below instead of raising ``IndexError``.
        if index + 2 < len(tokens) and tokens[index + 1] == "-":
            comparators.extend(_hyphen(token, tokens[index + 2]))
            index += 3
            continue
        if token == "-":
            raise SemVerError(f"hyphen range in {text!r} needs a version on both sides")

        operator = ""
        for candidate in _OPERATORS:
            if token.startswith(candidate):
                operator = candidate
                token = token[len(candidate):]
                break
        if not token:
            raise SemVerError(f"range {text!r}: operator {operator!r} has no version")

        # Build metadata inside a range token is parsed for validation and then
        # dropped: precedence ignores it, so ``>=1.2.3+sha`` is just ``>=1.2.3``.
        parts, prerelease, _build = _parse_partial(token)
        comparators.extend(_expand(operator, parts[0], parts[1], parts[2], prerelease))
        index += 1

    return ComparatorSet(tuple(comparators))


def parse_range(text: str) -> Range:
    """Shorthand for :meth:`Range.parse`."""
    return Range.parse(text)


def satisfies(version, spec, *, include_prerelease: bool = False) -> bool:
    """True if ``version`` falls inside ``spec``.

    ``version`` may be a :class:`Version` or a strict version string; ``spec``
    may be a :class:`Range` or a range string.
    """
    if isinstance(version, str):
        version = Version.parse(version)
    if not isinstance(version, Version):
        raise SemVerError(f"expected a Version or str, got {type(version).__name__}")
    return Range.parse(spec).matches(version, include_prerelease=include_prerelease)


def sort_versions(versions: Iterable, *, reverse: bool = False) -> list:
    """Sort versions (or version strings) by precedence. Stable."""
    prepared = [v if isinstance(v, Version) else Version.parse(v) for v in versions]
    return sorted(prepared, key=lambda v: v.precedence_key(), reverse=reverse)


def _best(versions: Iterable, spec, *, include_prerelease: bool, want_max: bool):
    """Shared body of :func:`max_satisfying` / :func:`min_satisfying`.

    Iterates once, keeps no list, and compares by precedence — so two versions
    that differ only in build metadata tie and the first one seen wins.
    """
    target = Range.parse(spec)
    best: Optional[Version] = None
    for item in versions:
        version = item if isinstance(item, Version) else Version.parse(item)
        if not target.matches(version, include_prerelease=include_prerelease):
            continue
        if best is None:
            best = version
        elif want_max and version > best:
            best = version
        elif not want_max and version < best:
            best = version
    return best


def max_satisfying(versions: Iterable, spec, *, include_prerelease: bool = False):
    """Highest version in ``versions`` that satisfies ``spec``, or ``None``."""
    return _best(versions, spec, include_prerelease=include_prerelease, want_max=True)


def min_satisfying(versions: Iterable, spec, *, include_prerelease: bool = False):
    """Lowest version in ``versions`` that satisfies ``spec``, or ``None``."""
    return _best(versions, spec, include_prerelease=include_prerelease, want_max=False)
