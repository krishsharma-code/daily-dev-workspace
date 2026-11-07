"""Tests for semver_kit: parsing, precedence, bumping, and range matching."""

import pytest

from semver_kit import (
    Comparator,
    ComparatorSet,
    Range,
    SemVerError,
    Version,
    max_satisfying,
    min_satisfying,
    parse,
    parse_range,
    satisfies,
    sort_versions,
)


def v(text):
    return Version.parse(text)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, major, minor, patch, prerelease, build",
    [
        ("0.0.0", 0, 0, 0, (), ()),
        ("1.2.3", 1, 2, 3, (), ()),
        ("10.20.30", 10, 20, 30, (), ()),
        ("1.0.0-alpha", 1, 0, 0, ("alpha",), ()),
        ("1.0.0-alpha.1", 1, 0, 0, ("alpha", "1"), ()),
        ("1.0.0-0.3.7", 1, 0, 0, ("0", "3", "7"), ()),
        ("1.0.0-x.7.z.92", 1, 0, 0, ("x", "7", "z", "92"), ()),
        ("1.0.0-x-y-z.--", 1, 0, 0, ("x-y-z", "--"), ()),
        ("1.0.0+20130313144700", 1, 0, 0, (), ("20130313144700",)),
        ("1.0.0-beta+exp.sha.5114f85", 1, 0, 0, ("beta",), ("exp", "sha", "5114f85")),
        ("1.0.0+21AF26D3---117B344092BD", 1, 0, 0, (), ("21AF26D3---117B344092BD",)),
        ("  1.2.3  ", 1, 2, 3, (), ()),
        ("999999999999999999.0.0", 999999999999999999, 0, 0, (), ()),
    ],
)
def test_parse_valid(text, major, minor, patch, prerelease, build):
    version = v(text)
    assert version.release == (major, minor, patch)
    assert version.prerelease == prerelease
    assert version.build == build


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "1",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-",
        "1.2.3+",
        "1.2.3-alpha..1",
        "1.2.3-alpha.",
        "1.2.3-alpha_1",
        "1.2.3-alpha+",
        "1.2.3+build..1",
        "1.2.3-01",          # leading zero in a numeric prerelease identifier
        "v1.2.3",            # strict: no prefix
        "=1.2.3",
        "a.b.c",
        "1.2.3-+",
        "١.٢.٣",             # Arabic-Indic digits are not ASCII digits
        "1.2.3 - 1.2.4",
        "1.2.-3",
        "1..3",
    ],
)
def test_parse_invalid(text):
    with pytest.raises(SemVerError):
        Version.parse(text)


def test_parse_non_string():
    with pytest.raises(SemVerError):
        Version.parse(123)  # type: ignore[arg-type]


def test_parse_strips_surrounding_whitespace_only():
    assert v(" 1.2.3 ").release == (1, 2, 3)
    with pytest.raises(SemVerError):
        Version.parse("1.2 .3")


@pytest.mark.parametrize(
    "text",
    ["1.2.3", "1.2.3-alpha.1", "1.2.3+build.7", "1.2.3-rc.1+build.7", "0.0.0-0"],
)
def test_round_trip(text):
    assert str(v(text)) == text


def test_str_and_repr():
    version = v("1.2.3-rc.1+build.7")
    assert str(version) == "1.2.3-rc.1+build.7"
    assert repr(version) == "Version('1.2.3-rc.1+build.7')"
    assert version.base == "1.2.3"


def test_parse_alias():
    assert parse("2.0.0") == Version(2, 0, 0)


# --------------------------------------------------------------------------
# Direct construction
# --------------------------------------------------------------------------


def test_construct_with_string_prerelease():
    assert Version(1, 2, 3, "rc.1").prerelease == ("rc", "1")
    assert Version(1, 2, 3, ["rc", "1"]).prerelease == ("rc", "1")
    assert Version(1, 2, 3, build="a.b").build == ("a", "b")


@pytest.mark.parametrize(
    "args",
    [
        (1.0, 2, 3),
        ("1", 2, 3),
        (True, 2, 3),
        (-1, 2, 3),
        (1, 2, 3, ("01",)),
        (1, 2, 3, ("",)),
        (1, 2, 3, ("a b",)),
        (1, 2, 3, (1,)),
        (1, 2, 3, (), ("+",)),
    ],
)
def test_construct_invalid(args):
    with pytest.raises(SemVerError):
        Version(*args)


def test_frozen():
    version = v("1.2.3")
    with pytest.raises(Exception):
        version.major = 2  # type: ignore[misc]


# --------------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------------

SPEC_CHAIN = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
    "1.0.1",
    "1.1.0",
    "1.10.0",
    "2.0.0",
]


def test_spec_precedence_chain():
    versions = [v(text) for text in SPEC_CHAIN]
    for lower, higher in zip(versions, versions[1:]):
        assert lower < higher
        assert higher > lower
        assert not (lower == higher)
    assert versions == sorted(reversed(versions))
    assert versions == sort_versions(reversed(versions))


def test_numeric_prerelease_is_numeric_not_lexical():
    assert v("1.0.0-beta.2") < v("1.0.0-beta.11")
    assert v("1.0.0-alpha.9") < v("1.0.0-alpha.10")


def test_numeric_identifiers_sort_below_alphanumeric():
    assert v("1.0.0-1") < v("1.0.0-alpha")
    assert v("1.0.0-999") < v("1.0.0-a")


def test_shorter_prerelease_is_lower():
    assert v("1.0.0-alpha") < v("1.0.0-alpha.0")
    assert v("1.0.0-alpha.beta") < v("1.0.0-alpha.beta.1")


def test_build_metadata_ignored_in_precedence():
    assert v("1.0.0+a") == v("1.0.0+b")
    assert not (v("1.0.0+a") < v("1.0.0+b"))
    assert v("1.0.0+build.1") == v("1.0.0")
    assert v("1.0.0-rc.1+a") < v("1.0.0")


def test_identical_sees_build_metadata():
    assert v("1.0.0+a").identical(v("1.0.0+a"))
    assert not v("1.0.0+a").identical(v("1.0.0+b"))
    assert not v("1.0.0").identical(v("1.0.0+a"))


def test_hash_follows_precedence():
    assert len({v("1.0.0+a"), v("1.0.0+b"), v("1.0.0")}) == 1
    assert len({v("1.0.0"), v("1.0.1")}) == 2


def test_comparison_with_non_version():
    assert (v("1.0.0") == "1.0.0") is False
    with pytest.raises(TypeError):
        v("1.0.0") < "1.0.1"


def test_release_and_prerelease_properties():
    assert v("1.2.3-rc.1").is_prerelease
    assert not v("1.2.3").is_prerelease
    assert v("1.2.3+5").is_prerelease is False


def test_sort_versions_accepts_strings_and_reverse():
    assert sort_versions(["2.0.0", "1.0.0", "1.10.0"]) == [v("1.0.0"), v("1.10.0"), v("2.0.0")]
    assert sort_versions(["1.0.0", "2.0.0"], reverse=True) == [v("2.0.0"), v("1.0.0")]


def test_sort_versions_is_stable():
    a, b = v("1.0.0+x"), v("1.0.0+y")
    assert sort_versions([a, b]) == [a, b]
    assert sort_versions([b, a]) == [b, a]


# --------------------------------------------------------------------------
# Bumping
# --------------------------------------------------------------------------


def test_bump_patch():
    assert str(v("1.2.3").bump_patch()) == "1.2.4"
    assert str(v("1.2.3+build").bump_patch()) == "1.2.4"


def test_bump_minor():
    assert str(v("1.2.3").bump_minor()) == "1.3.0"
    assert str(v("1.2.3-rc.1").bump_minor()) == "1.3.0"


def test_bump_major():
    assert str(v("1.2.3").bump_major()) == "2.0.0"
    assert str(v("0.1.2").bump_major()) == "1.0.0"


def test_bump_releases_a_pending_prerelease():
    assert str(v("2.0.0-rc.3").bump_major()) == "2.0.0"
    assert str(v("1.3.0-beta.1").bump_minor()) == "1.3.0"
    assert str(v("1.2.3-rc.1").bump_patch()) == "1.2.3"


def test_bump_prerelease_from_release():
    assert str(v("1.2.3").bump_prerelease()) == "1.2.4-0"


def test_bump_prerelease_increments_last_numeric():
    assert str(v("1.2.3-0").bump_prerelease()) == "1.2.3-1"
    assert str(v("1.2.3-rc.1").bump_prerelease()) == "1.2.3-rc.2"
    assert str(v("1.2.3-rc.9") .bump_prerelease()) == "1.2.3-rc.10"


def test_bump_prerelease_appends_zero():
    assert str(v("1.2.3-beta").bump_prerelease()) == "1.2.3-beta.0"


def test_bump_prerelease_with_identifier():
    assert str(v("1.2.3-rc.1").bump_prerelease("beta")) == "1.2.3-beta.0"
    assert str(v("1.2.3-beta.4").bump_prerelease("beta")) == "1.2.3-beta.5"
    assert str(v("1.2.3").bump_prerelease("alpha")) == "1.2.3-alpha.0"


def test_bump_prerelease_rejects_bad_identifier():
    with pytest.raises(SemVerError):
        v("1.2.3").bump_prerelease("bad ident")
    with pytest.raises(SemVerError):
        v("1.2.3").bump_prerelease("01")


def test_bump_drops_build_metadata():
    assert str(v("1.2.3-rc.1+build.9").bump_patch()) == "1.2.3"


# --------------------------------------------------------------------------
# Ranges: parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, expected",
    [
        ("1.2.3", "=1.2.3"),
        ("=1.2.3", "=1.2.3"),
        (">=1.2.3", ">=1.2.3"),
        (">1.2.3", ">1.2.3"),
        ("<1.2.3", "<1.2.3"),
        ("<=1.2.3", "<=1.2.3"),
        ("1.2.x", ">=1.2.0 <1.3.0"),
        ("1.2.*", ">=1.2.0 <1.3.0"),
        ("1.x", ">=1.0.0 <2.0.0"),
        ("*", "*"),
        ("", "*"),
        ("x", "*"),
        ("1.2", ">=1.2.0 <1.3.0"),
        ("1", ">=1.0.0 <2.0.0"),
        (">=1.2", ">=1.2.0"),
        (">1.2", ">=1.3.0"),
        ("<1.2", "<1.2.0"),
        ("<=1.2", "<1.3.0"),
        ("~1.2.3", ">=1.2.3 <1.3.0"),
        ("~1.2", ">=1.2.0 <1.3.0"),
        ("~1", ">=1.0.0 <2.0.0"),
        ("~>1.2.3", ">=1.2.3 <1.3.0"),
        ("^1.2.3", ">=1.2.3 <2.0.0"),
        ("^0.2.3", ">=0.2.3 <0.3.0"),
        ("^0.0.3", ">=0.0.3 <0.0.4"),
        ("^0.0", ">=0.0.0 <0.1.0"),
        ("^0", ">=0.0.0 <1.0.0"),
        ("^1.2", ">=1.2.0 <2.0.0"),
        ("^1.x", ">=1.0.0 <2.0.0"),
        ("^1.2.3-beta.1", ">=1.2.3-beta.1 <2.0.0"),
        (">=1.2.3 <2.0.0", ">=1.2.3 <2.0.0"),
        (">= 1.2.3 < 2.0.0", ">=1.2.3 <2.0.0"),
        ("1.2.3 - 2.3.4", ">=1.2.3 <=2.3.4"),
        ("1.2 - 2.3.4", ">=1.2.0 <=2.3.4"),
        ("1.2.3 - 2.3", ">=1.2.3 <2.4.0"),
        ("1.2.3 - 2", ">=1.2.3 <3.0.0"),
        ("1.2.3 - 2.3.4 >=1.5.0", ">=1.2.3 <=2.3.4 >=1.5.0"),
        ("v1.2.3", "=1.2.3"),
        ("1.0.0 || >=2.0.0", "=1.0.0 || >=2.0.0"),
        ("  ^1.2.3  ", ">=1.2.3 <2.0.0"),
    ],
)
def test_range_string_forms(spec, expected):
    assert str(parse_range(spec)) == expected


@pytest.mark.parametrize(
    "spec",
    [
        "1.2.3.4",
        ">=1.2.3.4",
        "^1.02.3",
        "1.2.x.4",
        "~",
        "^",
        ">=",
        "abc",
        "1.2.x-beta",
        "- 1.2.3",
        "1.2.3 -",
        "1.2.3 - - 2.0.0",
        "^^1.2.3",
        "1.2.3 ||",
    ],
)
def test_range_invalid(spec):
    with pytest.raises(SemVerError):
        parse_range(spec)


def test_range_parse_accepts_range_object():
    rng = parse_range("^1.0.0")
    assert Range.parse(rng) is rng


def test_range_requires_sets():
    with pytest.raises(SemVerError):
        Range(())


def test_comparator_validation():
    with pytest.raises(SemVerError):
        Comparator("!=", v("1.0.0"))
    with pytest.raises(SemVerError):
        Comparator("=", "1.0.0")  # type: ignore[arg-type]


def test_comparator_set_rejects_non_version():
    with pytest.raises(SemVerError):
        ComparatorSet((Comparator(">=", v("1.0.0")),)).matches("1.2.3")  # type: ignore[arg-type]


def test_empty_comparator_set_is_any():
    assert ComparatorSet().is_any
    assert str(ComparatorSet()) == "*"
    assert ComparatorSet().matches(v("9.9.9"))
    assert not ComparatorSet().matches(v("9.9.9-rc.1"))


# --------------------------------------------------------------------------
# Ranges: matching
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version, spec, expected",
    [
        ("1.2.3", "1.2.3", True),
        ("1.2.4", "1.2.3", False),
        ("1.2.3+build", "1.2.3", True),
        ("1.2.4", ">=1.2.3", True),
        ("1.2.3", ">=1.2.3", True),
        ("1.2.2", ">1.2.3", False),
        ("1.2.4", ">1.2.3", True),
        ("1.2.2", "<1.2.3", True),
        ("1.2.3", "<1.2.3", False),
        ("1.2.3", "<=1.2.3", True),
        ("1.9.9", "^1.2.3", True),
        ("2.0.0", "^1.2.3", False),
        ("1.2.2", "^1.2.3", False),
        ("0.2.9", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("0.0.3", "^0.0.3", True),
        ("0.0.4", "^0.0.3", False),
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        ("1.2.0", "~1.2", True),
        ("1.3.0", "~1.2", False),
        ("1.5.0", "1.x", True),
        ("2.0.0", "1.x", False),
        ("1.2.7", "1.2.x", True),
        ("1.3.0", "1.2.x", False),
        ("0.0.1", "*", True),
        ("5.0.0", "", True),
        ("2.5.0", "1.2.3 - 2.3.4", False),
        ("2.3.4", "1.2.3 - 2.3.4", True),
        ("1.2.2", "1.2.3 - 2.3.4", False),
        ("2.3.9", "1.2.3 - 2.3", True),
        ("2.4.0", "1.2.3 - 2.3", False),
        ("1.2.3", "1.0.0 || >=2.0.0", False),
        ("2.1.0", "1.0.0 || >=2.0.0", True),
        ("1.0.0", "1.0.0 || >=2.0.0", True),
        ("1.2.3", ">=1.2.3 <1.3.0", True),
        ("1.3.0", ">=1.2.3 <1.3.0", False),
        ("3.0.0", "^1.0.0 || ^3.0.0", True),
    ],
)
def test_satisfies(version, spec, expected):
    assert satisfies(version, spec) is expected


def test_satisfies_accepts_version_objects():
    assert satisfies(v("1.5.0"), parse_range("^1.0.0"))


def test_satisfies_rejects_wrong_type():
    with pytest.raises(SemVerError):
        satisfies(1.5, "^1.0.0")


# --------------------------------------------------------------------------
# Prerelease gating
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "version, spec, expected",
    [
        ("1.2.3-beta.5", "^1.2.3-beta.1", True),
        ("1.2.3-beta.1", "^1.2.3-beta.1", True),
        ("1.2.3-beta.0", "^1.2.3-beta.1", False),
        ("1.2.4-alpha", "^1.2.3-beta.1", False),
        ("1.3.0-rc.1", "^1.2.3-beta.1", False),
        ("1.2.3-rc.1", ">=1.2.3-beta.1", True),
        ("1.2.4-rc.1", ">=1.2.3-beta.1", False),
        ("1.2.3-rc.1", "*", False),
        ("1.2.3-rc.1", ">=1.0.0", False),
        ("2.0.0-alpha", ">=1.0.0", False),
        ("1.2.3-rc.1", "1.2.3-rc.1", True),
        ("1.2.3", "^1.2.3-beta.1", True),
    ],
)
def test_prerelease_gating(version, spec, expected):
    assert satisfies(version, spec) is expected


def test_include_prerelease_opt_in():
    assert satisfies("1.2.3-rc.1", "*", include_prerelease=True)
    assert satisfies("1.5.0-alpha", "^1.0.0", include_prerelease=True)
    # The flag only lifts the gate; it does not move the bounds. 2.0.0-alpha
    # still sorts below "<2.0.0" and so still matches.
    assert satisfies("2.0.0-alpha", "^1.0.0", include_prerelease=True) is True
    assert satisfies("1.5.0-alpha", "^1.0.0") is False


def test_prerelease_gating_per_set():
    spec = "^1.2.3-beta.1 || >=3.0.0"
    assert satisfies("1.2.3-beta.4", spec)
    assert satisfies("3.0.0", spec)
    assert satisfies("1.5.0-alpha", spec) is False


# --------------------------------------------------------------------------
# Selection helpers
# --------------------------------------------------------------------------

POOL = ["1.0.0", "1.2.0", "1.2.3", "1.3.0-rc.1", "1.9.9", "2.0.0", "2.1.0-rc.1"]


def test_max_satisfying():
    assert str(max_satisfying(POOL, "^1.2.0")) == "1.9.9"
    assert str(max_satisfying(POOL, "^1.0.0")) == "1.9.9"
    assert str(max_satisfying(POOL, ">=2.0.0")) == "2.0.0"


def test_max_satisfying_with_prereleases():
    assert str(max_satisfying(POOL, ">=1.3.0-rc.1 <2.0.0")) == "1.9.9"
    assert str(max_satisfying(POOL, "^2.1.0-rc.1")) == "2.1.0-rc.1"
    assert str(max_satisfying(POOL, ">=1.0.0", include_prerelease=True)) == "2.1.0-rc.1"


def test_min_satisfying():
    assert str(min_satisfying(POOL, "^1.2.0")) == "1.2.0"
    assert str(min_satisfying(POOL, ">=1.0.0")) == "1.0.0"


def test_selection_returns_none_when_nothing_matches():
    assert max_satisfying(POOL, "^5.0.0") is None
    assert min_satisfying([], "*") is None


def test_selection_accepts_version_objects():
    versions = [v(text) for text in POOL]
    assert max_satisfying(versions, "^1.0.0") == v("1.9.9")


def test_selection_ignores_build_metadata_for_ordering():
    pool = ["1.0.0+a", "1.0.0+b"]
    assert str(max_satisfying(pool, "^1.0.0")) == "1.0.0+a"


# --------------------------------------------------------------------------
# A realistic end-to-end scenario
# --------------------------------------------------------------------------


def test_resolve_highest_compatible_dependency():
    available = ["1.4.2", "1.9.0", "2.0.0-beta.1", "2.0.0", "2.3.1", "3.0.0"]
    assert str(max_satisfying(available, "^1.4.0")) == "1.9.0"
    assert str(max_satisfying(available, "^2.0.0")) == "2.3.1"
    assert str(max_satisfying(available, "^2.0.0-beta.1")) == "2.3.1"
    assert str(max_satisfying(available, ">=1.0.0 <2.0.0")) == "1.9.0"
    assert max_satisfying(available, "^4.0.0") is None
    assert str(max_satisfying(available, "^2.0.0", include_prerelease=True)) == "2.3.1"
