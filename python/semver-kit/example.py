"""Runnable demo for semver_kit: ordering, bumping, and range matching.

    python example.py
"""

from semver_kit import (
    SemVerError,
    Version,
    max_satisfying,
    parse_range,
    satisfies,
    sort_versions,
)

CHAIN = [
    "1.0.0-alpha",
    "1.0.0-alpha.1",
    "1.0.0-alpha.beta",
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",
    "1.0.0-rc.1",
    "1.0.0",
]


def section(title):
    print(title)
    print("-" * len(title))


def show_precedence():
    section("precedence: the chain from the SemVer spec")
    for index, version in enumerate(sort_versions(CHAIN), start=1):
        print(f"  {index}. {version}")
    print("  beta.2 < beta.11 because numeric identifiers compare numerically")
    print("  1.0.0+build.9 == 1.0.0 because build metadata is ignored")
    print()


def show_bumps():
    section("bumping")
    rows = ["1.2.3", "1.2.3-rc.1", "2.0.0-beta.4", "1.4.0+build.7"]
    print(f"  {'from':>16}  {'patch':>12}  {'minor':>10}  {'major':>9}  {'pre':>14}")
    for text in rows:
        version = Version.parse(text)
        print(
            f"  {text:>16}  {str(version.bump_patch()):>12}  "
            f"{str(version.bump_minor()):>10}  {str(version.bump_major()):>9}  "
            f"{str(version.bump_prerelease()):>14}"
        )
    print()


def show_ranges():
    section("range matching")
    specs = ["^1.2.3", "~1.2.3", "1.2.x", ">=1.5.0 <2.0.0", "1.2.3 - 1.9.9"]
    candidates = ["1.2.2", "1.2.3", "1.2.9", "1.3.0", "1.9.9", "2.0.0"]

    header = "".join(f"{c:>9}" for c in candidates)
    print(f"  {'range':>18}{header}")
    for spec in specs:
        cells = "".join(f"{'yes' if satisfies(c, spec) else '.':>9}" for c in candidates)
        print(f"  {spec:>18}{cells}")
    print()

    for spec in specs:
        print(f"  {spec:>18}  ->  {parse_range(spec)}")
    print()


def show_prerelease_gate():
    section("prereleases are opt-in per range")
    cases = [
        ("1.2.3-beta.5", "^1.2.3-beta.1"),
        ("1.2.4-alpha", "^1.2.3-beta.1"),
        ("1.3.0-rc.1", "^1.2.3-beta.1"),
        ("1.3.0-rc.1", ">=1.0.0"),
    ]
    for version, spec in cases:
        verdict = "matches" if satisfies(version, spec) else "excluded"
        print(f"  {version:>14}  {spec:>18}  {verdict}")
    print("  a prerelease needs a comparator at the same triple to be in range")
    print()


def show_resolution():
    section("picking a version to install")
    available = ["1.4.2", "1.9.0", "2.0.0-beta.1", "2.0.0", "2.3.1", "3.0.0"]
    print(f"  available: {', '.join(available)}")
    for spec in ["^1.4.0", "^2.0.0", "^2.0.0-beta.1", "^4.0.0"]:
        best = max_satisfying(available, spec)
        print(f"  {spec:>18}  ->  {best if best is not None else 'no match'}")
    print()


def show_errors():
    section("everything invalid raises SemVerError")
    for bad in ["1.2", "01.2.3", "1.2.3-01", "v1.2.3", "1.2.3-beta..1"]:
        try:
            Version.parse(bad)
        except SemVerError as error:
            print(f"  {bad!r:>18}  {error}")
    for bad in ["^", "1.2.x.4", "1.2.3 ||"]:
        try:
            parse_range(bad)
        except SemVerError as error:
            print(f"  {bad!r:>18}  {error}")
    # Hostile input stays on the same exception type: the digit cap keeps
    # int()'s own ValueError from escaping.
    huge = "1" * 5000
    for label, bad in (("<5000 digits>.0.0", f"{huge}.0.0"), ("1.2.3-<5000 digits>", f"1.2.3-{huge}")):
        try:
            Version.parse(bad)
        except SemVerError as error:
            print(f"  {label:>18}  {error}")
    print()


def main():
    show_precedence()
    show_bumps()
    show_ranges()
    show_prerelease_gate()
    show_resolution()
    show_errors()


if __name__ == "__main__":
    main()
