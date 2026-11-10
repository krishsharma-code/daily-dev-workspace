"""Tests for the input-limits hardening of semver_kit.

Two defects motivated this file:

1. ``Version.parse`` and ``_parse_partial`` fed numeric parts straight to
   ``int()``. On CPython 3.11+ a component longer than
   ``sys.get_int_max_str_digits()`` (4300 by default) makes ``int()`` raise a
   *bare* ``ValueError`` — not a ``SemVerError`` — so a caller following the
   README ("catch ``SemVerError``") crashed on hostile input. On older
   interpreters the same input burned quadratic CPU.
2. A numeric *prerelease* identifier was never converted during parsing, so
   ``Version.parse("1.2.3-" + "1" * 5000)`` succeeded and only exploded later,
   inside ``precedence_key()``, the first time anything sorted it.

The fix routes every conversion through a bounded helper, so these tests assert
the shape of the contract: hostile input raises ``SemVerError``, promptly, from
every entry point.
"""

import pytest

from semver_kit import (
    SemVerError,
    Version,
    _MAX_NUMERIC_DIGITS,
    max_satisfying,
    parse_range,
    satisfies,
    sort_versions,
)

# Comfortably past both our own cap and CPython's int-string limit, so the
# test would fail loudly if the guard were removed.
HOSTILE_DIGITS = "1" * 5000
OVER_CAP = "1" * (_MAX_NUMERIC_DIGITS + 1)
AT_CAP = "1" * _MAX_NUMERIC_DIGITS


# --------------------------------------------------------------------------
# The exception contract: SemVerError, never a bare ValueError
# --------------------------------------------------------------------------


def test_long_release_component_raises_semver_error():
    with pytest.raises(SemVerError):
        Version.parse(f"{HOSTILE_DIGITS}.2.3")


def test_long_release_component_is_also_a_value_error():
    # SemVerError subclasses ValueError, so one ``except`` catches both a bad
    # version and a hostile one. Guard the inheritance while we are here.
    with pytest.raises(ValueError):
        Version.parse(f"1.{HOSTILE_DIGITS}.3")


@pytest.mark.parametrize(
    "spec",
    [
        f"^{HOSTILE_DIGITS}.2.3",
        f">={HOSTILE_DIGITS}.0.0",
        f"~1.{HOSTILE_DIGITS}.0",
        f"1.0.0 - {HOSTILE_DIGITS}.0.0",
        f"<=1.0.0 || {HOSTILE_DIGITS}.0.0",
    ],
)
def test_long_component_in_range_raises_semver_error(spec):
    with pytest.raises(SemVerError):
        parse_range(spec)


def test_satisfies_does_not_leak_a_bare_value_error():
    with pytest.raises(SemVerError):
        satisfies(f"{HOSTILE_DIGITS}.0.0", "*")


def test_sort_does_not_leak_a_bare_value_error():
    with pytest.raises(SemVerError):
        sort_versions([f"1.2.3-{HOSTILE_DIGITS}"])


# --------------------------------------------------------------------------
# Numeric prerelease identifiers are validated at construction
# --------------------------------------------------------------------------


def test_long_numeric_prerelease_identifier_rejected_by_parse():
    with pytest.raises(SemVerError):
        Version.parse(f"1.2.3-{HOSTILE_DIGITS}")


def test_long_numeric_prerelease_identifier_rejected_by_constructor():
    # The constructor is a public entry point too: it must not be possible to
    # build a Version that only breaks when it is first compared.
    with pytest.raises(SemVerError):
        Version(1, 2, 3, (HOSTILE_DIGITS,))


def test_long_numeric_prerelease_identifier_rejected_via_string_argument():
    with pytest.raises(SemVerError):
        Version(1, 2, 3, f"rc.{HOSTILE_DIGITS}")


def test_rejection_message_names_the_limit():
    with pytest.raises(SemVerError) as excinfo:
        Version.parse(f"1.2.3-{HOSTILE_DIGITS}")
    assert str(_MAX_NUMERIC_DIGITS) in str(excinfo.value)


# --------------------------------------------------------------------------
# The cap is a boundary, not a blanket refusal
# --------------------------------------------------------------------------


def test_component_exactly_at_the_cap_is_accepted():
    version = Version.parse(f"{AT_CAP}.0.0")
    assert len(str(version.major)) == _MAX_NUMERIC_DIGITS


def test_component_one_digit_past_the_cap_is_rejected():
    with pytest.raises(SemVerError):
        Version.parse(f"{OVER_CAP}.0.0")


def test_prerelease_identifier_at_the_cap_is_accepted():
    version = Version.parse(f"1.2.3-{AT_CAP}")
    assert version.prerelease == (AT_CAP,)


def test_prerelease_identifier_past_the_cap_is_rejected():
    with pytest.raises(SemVerError):
        Version.parse(f"1.2.3-{OVER_CAP}")


# --------------------------------------------------------------------------
# Behaviour preserved for everything realistic
# --------------------------------------------------------------------------


def test_at_cap_versions_still_sort_numerically():
    big = Version.parse(f"{AT_CAP}.0.0")
    small = Version.parse("1.0.0")
    assert sort_versions([big, small]) == [small, big]


def test_at_cap_prerelease_still_sorts_below_its_release():
    prerelease = Version.parse(f"1.2.3-{AT_CAP}")
    release = Version.parse("1.2.3")
    assert sort_versions([release, prerelease]) == [prerelease, release]


def test_at_cap_prerelease_still_bumps():
    # bump_prerelease converts the identifier back to int; it must stay on the
    # guarded path and still work at the cap.
    assert str(Version.parse(f"1.2.3-{AT_CAP}").bump_prerelease()) == f"1.2.3-{int(AT_CAP) + 1}"


def test_build_identifiers_are_not_capped():
    # Build metadata is never converted to int, so there is no reason to limit
    # it — and real-world build identifiers (git SHAs, timestamps) can be long.
    long_build = "b" * 5000
    version = Version.parse(f"1.2.3+{long_build}")
    assert version.build == (long_build,)
    assert str(version) == f"1.2.3+{long_build}"


def test_build_metadata_still_ignored_in_ranges():
    assert satisfies("1.2.3+sha.abc", ">=1.2.3")


def test_selection_still_works_with_large_but_legal_versions():
    pool = [f"{AT_CAP}.0.0", "1.0.0", "2.0.0"]
    assert str(max_satisfying(pool, "*")) == f"{AT_CAP}.0.0"
