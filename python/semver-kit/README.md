# semver-kit

Semantic Versioning 2.0.0 done properly: parse, order, bump, and match
npm-style ranges. Standard library only — no `packaging`, no `semver` from
PyPI, no regex you have to trust.

It answers the three questions that actually come up in a build or release
script: *is this string a valid version?*, *which of these versions is newer?*,
and *does this version satisfy `^1.2.3`?*

```python
from semver_kit import Version, max_satisfying, satisfies, sort_versions

Version.parse("1.2.3-rc.1+build.5").release      # (1, 2, 3)
satisfies("1.4.9", "^1.2.3")                    # True
max_satisfying(["1.4.2", "1.9.0", "2.0.0"], "^1.0.0")   # Version('1.9.0')
sort_versions(["1.10.0", "1.9.0", "1.2.0"])     # 1.2.0, 1.9.0, 1.10.0
```

## API

| Name | Purpose |
| --- | --- |
| `Version(major, minor, patch, prerelease=(), build=())` | Frozen, validated. Ordering and equality use **precedence**. |
| `Version.parse(text)` / `parse(text)` | Strict `MAJOR.MINOR.PATCH[-pre][+build]` parser. |
| `v.release` | `(major, minor, patch)` — no prerelease, no build. |
| `v.is_prerelease`, `v.base` | `True` for `1.2.3-rc.1`; `"1.2.3"` as text. |
| `v.precedence_key()` | The sort key, if you want to sort inside your own structure. |
| `v.identical(other)` | Equality *including* build metadata. |
| `v.bump_major()`, `bump_minor()`, `bump_patch()` | Next release; a pending prerelease is released rather than skipped. |
| `v.bump_prerelease(identifier=None)` | Increment the last numeric prerelease identifier. |
| `Range.parse(text)` / `parse_range(text)` | A `\|\|` union of comparator sets. |
| `range.matches(version, include_prerelease=False)` | Membership test. |
| `satisfies(version, spec, *, include_prerelease=False)` | The one-liner; accepts strings or objects on both sides. |
| `max_satisfying(versions, spec)` / `min_satisfying(...)` | Highest/lowest match, or `None`. |
| `sort_versions(versions, *, reverse=False)` | Stable precedence sort; accepts strings or `Version`s. |
| `Comparator`, `ComparatorSet` | The parsed pieces, if you want to inspect or print a range. |

Everything invalid raises `SemVerError`, a `ValueError` subclass: a bad
version, a bad range, a `bool` where a number belongs, a non-string, a range
operator with nothing after it.

### Input limits

That contract holds for hostile input too. A numeric component — a release
part or a numeric prerelease identifier — is capped at **64 digits**; anything
longer raises `SemVerError`. Without the cap, `int()` on a 5000-digit "version"
would either burn quadratic CPU or raise a bare `ValueError` that slipped
straight past the `SemVerError` a caller was told to catch. No real version
component is anywhere near 64 digits, and build metadata is not capped because
it is never converted to an integer.

This is a parser boundary, not a sanitiser: it decides whether a string *is* a
version. If the string came off the network, cap its total length yourself
before calling in.

## Precedence, including the parts people get wrong

Build metadata is ignored — `1.0.0+a == 1.0.0+b`, and neither is "greater".
Prerelease identifiers are compared piece by piece: numeric ones numerically
(`beta.2 < beta.11`, not lexical), numeric before alphanumeric (`1.0.0-1 <
1.0.0-alpha`), and a shorter list before a longer one that extends it
(`alpha < alpha.0`). A prerelease always sorts below its release.

The spec's own chain sorts exactly as written:

```
1.0.0-alpha < 1.0.0-alpha.1 < 1.0.0-alpha.beta < 1.0.0-beta
  < 1.0.0-beta.2 < 1.0.0-beta.11 < 1.0.0-rc.1 < 1.0.0
```

Because equality ignores build metadata, so does hashing — `{v1, v2}` dedupes
versions that differ only in `+build`. When you need to keep them apart, use
`identical()`.

## Bumping

| From | `bump_patch` | `bump_minor` | `bump_major` | `bump_prerelease` |
| --- | --- | --- | --- | --- |
| `1.2.3` | `1.2.4` | `1.3.0` | `2.0.0` | `1.2.4-0` |
| `1.2.3-rc.1` | `1.2.3` | `1.3.0` | `2.0.0` | `1.2.3-rc.2` |
| `2.0.0-beta.4` | `2.0.0` | `2.0.0` | `2.0.0` | `2.0.0-beta.5` |

`2.0.0-beta.4` bumped major is `2.0.0`, not `3.0.0`: the version was already
heading for 2.0.0 and the prerelease was the last step towards it. Same rule
for `1.3.0-beta.1` bumped minor. Every bump drops build metadata.

## Ranges

`||` separates alternatives, whitespace inside one alternative means *and*, and
`>= 1.2.3` with a space after the operator is accepted.

| Spec | Means |
| --- | --- |
| `1.2.3` | `=1.2.3` — exactly that version, by precedence |
| `1.2`, `1.2.x`, `1.2.*` | `>=1.2.0 <1.3.0` |
| `1`, `1.x` | `>=1.0.0 <2.0.0` |
| `*`, `x`, `` (empty) | any version — prereleases excluded, see below |
| `>=1.2` | `>=1.2.0` |
| `>1.2` | `>=1.3.0` — strictly greater than the whole `1.2` line |
| `<1.2` | `<1.2.0` |
| `<=1.2` | `<1.3.0` |
| `~1.2.3` | `>=1.2.3 <1.3.0` — patch-level drift |
| `~1.2` | `>=1.2.0 <1.3.0` |
| `~1` | `>=1.0.0 <2.0.0` |
| `^1.2.3` | `>=1.2.3 <2.0.0` |
| `^0.2.3` | `>=0.2.3 <0.3.0` — the caret pins the leftmost non-zero part |
| `^0.0.3` | `>=0.0.3 <0.0.4` |
| `1.2.3 - 2.3.4` | `>=1.2.3 <=2.3.4`, inclusive at both ends |
| `1.2.3 - 2.3` | `>=1.2.3 <2.4.0` |
| `1.2.3 - 2` | `>=1.2.3 <3.0.0` |

The caret rule is the one that bites: under `^0.x`, minor bumps are breaking,
so `^0.2.3` allows `0.2.9` but never `0.3.0`.

## Prereleases are opt-in, per range

A prerelease only satisfies a range if the range itself mentions a prerelease
at the *same* `major.minor.patch`:

```python
satisfies("1.2.3-beta.5", "^1.2.3-beta.1")   # True
satisfies("1.2.4-alpha",  "^1.2.3-beta.1")   # False — different triple
satisfies("1.3.0-rc.1",   "^1.2.3-beta.1")   # False
satisfies("1.3.0-rc.1",   ">=1.0.0")         # False — stable range, pre version
```

This is npm's rule and it is the right one: without it, `>=1.0.0` would pull in
`2.0.0-alpha` and a resolver would happily install a beta nobody asked for.

`include_prerelease=True` lifts the gate so prereleases are candidates
anywhere. Note what it does *not* do: it does not move the bounds. `2.0.0-alpha`
still sorts below `<2.0.0`, so it matches `^1.0.0` under that flag. Opt in only
when you are deliberately resolving a prerelease channel.

## Strictness is deliberate

`Version.parse` accepts exactly what the spec allows — three numeric parts, no
leading zeros, ASCII digits only, no `v` prefix, no `=`. That makes it usable
as a validator: `parse("1.2")`, `parse("v1.2.3")`, `parse("01.2.3")` and
`parse("1.2.3-01")` all raise, and the message says which part was wrong.
Unicode digits are rejected too — `"١.٢.٣"` looks numeric to `str.isdigit()` and
`int()` would quietly turn it into `123`.

Ranges are more forgiving where manifests are, because real `package.json`
files contain `v1.2.3`. A leading `v` is accepted *in a range*, never in a
strict version.

## What this does not do

- **No resolution graph.** `max_satisfying` picks from a list you already have.
  No transitive dependency walk, no conflict explanation, no lockfile.
- **No dates or channels.** Dist-tags (`latest`, `next`) are registry concepts;
  this module only compares versions.
- **No rewriting of ranges.** `str(range)` prints the parsed comparators
  (`^1.2.3` prints as `>=1.2.3 <2.0.0`), which is deliberate — it shows you
  what was actually understood rather than echoing your input.
- **No `!=`.** SemVer has no such operator; compose with `||` instead.

## Layout

```
python/semver-kit/
├── README.md
├── semver_kit.py            # implementation
├── example.py               # runnable demo
├── conftest.py              # makes the module importable in tests
└── tests/
    ├── test_semver_kit.py     # parsing, precedence, bumping, ranges
    └── test_input_limits.py   # the SemVerError contract under hostile input
```

## Run the demo

```bash
cd python/semver-kit
python example.py
```

It prints the spec's precedence chain, a bump table, a range-versus-version
match grid, the prerelease gate in action, a "pick a version to install"
resolution, and the error message for each kind of malformed input.

## Run the tests

```bash
cd python/semver-kit
python -m pytest
```

221 tests: every valid and invalid parse shape, the spec's precedence chain and
each individual tie-break rule, build-metadata equality and hashing, all four
bump operations including prerelease release, every range form and its
expansion, hyphen ranges, unions, partial and wildcard operators (`>1.2`,
`<=1.2`, `<1.2`), the prerelease gate per alternative, `include_prerelease`,
selection helpers, a realistic dependency-resolution scenario, and the
input-limit boundary — that a 5000-digit component raises `SemVerError` (not a
bare `ValueError`) from `parse`, `parse_range`, `satisfies` and `sort_versions`,
that 64 digits still works, and that build identifiers stay uncapped.
`pytest` is needed only to run the suite.
