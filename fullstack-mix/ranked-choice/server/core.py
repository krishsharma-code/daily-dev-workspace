"""Instant-runoff (ranked-choice) tabulation core.

Pure, dependency-free logic that answers one question: given a roster of
candidates and a pile of ranked ballots, who wins a single-seat election run
under instant-runoff voting?

The rules implemented here are the ones most IRV statutes share:

1. Count every ballot for its highest-ranked candidate who is still in the
   race. A ballot whose whole ranking has been eliminated is *exhausted* and
   stops counting.
2. If a candidate holds a strict majority of the votes that are still counting,
   they win.
3. Otherwise the candidate with the fewest votes is eliminated and the count
   repeats.

Two details are decided here rather than left to chance, and both are reported
in the output so a reader can check them:

* **The majority is a majority of continuing votes**, not of all ballots cast.
  Exhausted ballots lower the bar, which is why every round reports both
  ``active_votes`` and ``exhausted``.
* **A tie for last place eliminates exactly one candidate** — the one with
  fewest first-preference votes, then alphabetically by name. The round record
  lists the tied names in ``tie_break`` so an arbitrary-looking elimination is
  always visible.

Input is text-first (``Aditi > Bharat > Chen``), so the same parser serves the
API, the tests and anything else that can produce a string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

__all__ = [
    "InputError",
    "Ballot",
    "MAX_BALLOTS",
    "MAX_CANDIDATES",
    "MAX_WEIGHT",
    "condorcet_winner",
    "derive_candidates",
    "pairwise_matrix",
    "parse_ballots",
    "parse_candidates",
    "run",
    "tabulate",
]

#: Hard limits, so a pasted spreadsheet cannot spin the server.
MAX_CANDIDATES = 50
MAX_BALLOTS = 10_000
MAX_WEIGHT = 1_000_000
MAX_NAME_LENGTH = 80

#: Characters that would make a ballot line ambiguous.
_RESERVED_CHARS = (">", ",", ":", "\n", "\r")

_SEPARATORS = re.compile(r"\s*(?:>|,)\s*")
_WEIGHT_PREFIX = re.compile(r"^(?P<weight>\d{1,7})\s*:\s*(?P<rest>.*)$")


class InputError(ValueError):
    """Raised for anything the caller can fix by correcting their input."""


@dataclass(frozen=True)
class Ballot:
    """One voter's ranking, highest preference first.

    ``weight`` lets a block of identical ballots be written as a single line
    (``3: Goa > Kochi``); ``line`` is the 1-based source line, kept so an error
    or a surprising count can be traced back to the text the user pasted.
    """

    ranking: tuple[str, ...]
    weight: int = 1
    line: int = 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _clean_name(raw: str, where: str) -> str:
    name = " ".join(raw.split())
    if not name:
        raise InputError(f"{where}: candidate name is empty")
    if len(name) > MAX_NAME_LENGTH:
        raise InputError(
            f"{where}: candidate name is longer than {MAX_NAME_LENGTH} characters"
        )
    for char in _RESERVED_CHARS:
        if char in name:
            shown = "newline" if char in ("\n", "\r") else char
            raise InputError(f"{where}: candidate name may not contain {shown!r}")
    return name


def parse_candidates(raw: object) -> list[str]:
    """Turn a roster (list of names, or a comma/newline separated string) into
    a validated, de-duplicated list of names in declaration order."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw
        items = re.split(r"[,\n]", text)
    elif isinstance(raw, (list, tuple)):
        items = []
        for entry in raw:
            if not isinstance(entry, str):
                raise InputError("candidates: every candidate must be a string")
            items.append(entry)
    else:
        raise InputError("candidates: expected a list of names or a string")

    names: list[str] = []
    seen: dict[str, str] = {}
    for index, item in enumerate(items, start=1):
        if not item.strip():
            continue
        name = _clean_name(item, f"candidate {index}")
        key = name.casefold()
        if key in seen:
            raise InputError(f"candidate {index}: {name!r} is declared twice")
        seen[key] = name
        names.append(name)

    if len(names) > MAX_CANDIDATES:
        raise InputError(f"candidates: at most {MAX_CANDIDATES} are supported")
    return names


def _split_ranking(text: str, line_no: int) -> list[str]:
    """Split one ballot line into names, rejecting empty entries.

    ``Aditi >> Bharat`` and a bare ``>`` are both mistakes worth naming rather
    than silently collapsing into a shorter ranking.
    """
    parts = _SEPARATORS.split(text.strip())
    for part in parts:
        if not part.strip():
            raise InputError(f"line {line_no}: empty entry between separators")
    return [part.strip() for part in parts]


def parse_ballots(
    text: str,
    candidates: Optional[Sequence[str]] = None,
) -> tuple[list[Ballot], list[str]]:
    """Parse ranked ballots and return ``(ballots, candidates)``.

    Each non-blank line is one ballot: names separated by ``>`` or ``,``,
    highest preference first. An optional ``N:`` prefix repeats the ballot
    ``N`` times. Blank lines and lines starting with ``#`` are ignored.

    When ``candidates`` is empty the roster is derived from the ballots in
    first-appearance order. Otherwise every name must match the roster
    case-insensitively, and the roster's spelling is what the report uses.
    """
    if not isinstance(text, str):
        raise InputError("ballots: expected a string of ranked lines")

    roster = parse_candidates(candidates)
    derive_mode = not roster
    lookup = {name.casefold(): name for name in roster}
    derived: list[str] = []

    ballots: list[Ballot] = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        weight = 1
        match = _WEIGHT_PREFIX.match(line)
        if match:
            weight = int(match.group("weight"))
            if weight < 1:
                raise InputError(f"line {line_no}: ballot weight must be at least 1")
            if weight > MAX_WEIGHT:
                raise InputError(
                    f"line {line_no}: ballot weight may not exceed {MAX_WEIGHT}"
                )
            line = match.group("rest")
            if not line.strip():
                raise InputError(f"line {line_no}: ballot has no candidates")

        ranking: list[str] = []
        for raw_name in _split_ranking(line, line_no):
            key = raw_name.casefold()
            if not derive_mode:
                if key not in lookup:
                    raise InputError(
                        f"line {line_no}: {raw_name!r} is not a declared candidate"
                    )
                name = lookup[key]
            else:
                if key in lookup:
                    name = lookup[key]
                else:
                    name = _clean_name(raw_name, f"line {line_no}")
                    derived.append(name)
                    lookup[key] = name
            if name in ranking:
                raise InputError(f"line {line_no}: {name!r} is ranked twice")
            ranking.append(name)

        if len(ranking) > MAX_CANDIDATES:
            raise InputError(
                f"line {line_no}: at most {MAX_CANDIDATES} candidates per ballot"
            )

        ballots.append(Ballot(ranking=tuple(ranking), weight=weight, line=line_no))
        if len(ballots) > MAX_BALLOTS:
            raise InputError(f"ballots: at most {MAX_BALLOTS} lines are supported")

    return ballots, roster or derived


def derive_candidates(ballots: Iterable[Ballot]) -> list[str]:
    """Names in first-appearance order across ``ballots``."""
    names: list[str] = []
    seen: set[str] = set()
    for ballot in ballots:
        for name in ballot.ranking:
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------


def _first_choice(ballot: Ballot, eliminated: set[str]) -> Optional[str]:
    for name in ballot.ranking:
        if name not in eliminated:
            return name
    return None


def _round_tally(
    ballots: Sequence[Ballot],
    remaining: Sequence[str],
    eliminated: set[str],
) -> tuple[dict[str, int], int, int]:
    """Return ``(tally, active_votes, exhausted_votes)`` for one round."""
    tally = {name: 0 for name in remaining}
    exhausted = 0
    for ballot in ballots:
        choice = _first_choice(ballot, eliminated)
        if choice is None:
            exhausted += ballot.weight
        else:
            tally[choice] += ballot.weight
    return tally, sum(tally.values()), exhausted


def pairwise_matrix(
    candidates: Sequence[str],
    ballots: Sequence[Ballot],
) -> dict[str, dict[str, int]]:
    """``matrix[a][b]`` is the weight of ballots ranking ``a`` above ``b``.

    A ballot that ranks only one of the pair says nothing about the other, so
    it contributes to neither direction.
    """
    names = list(candidates)
    matrix = {name: {other: 0 for other in names if other != name} for name in names}
    for ballot in ballots:
        ranked = ballot.ranking
        for index, name in enumerate(ranked):
            if name not in matrix:
                continue
            row = matrix[name]
            for lower in ranked[index + 1 :]:
                if lower in row:
                    row[lower] += ballot.weight
    return matrix


def condorcet_winner(
    candidates: Sequence[str],
    matrix: dict[str, dict[str, int]],
) -> Optional[str]:
    """The candidate who beats every other head-to-head, if there is one.

    Returns ``None`` when the pairwise result is tied or cyclic — which is the
    interesting case, because instant-runoff can then elect someone who loses
    a head-to-head to another candidate.
    """
    names = list(candidates)
    for name in names:
        if all(
            matrix[name][other] > matrix[other][name]
            for other in names
            if other != name
        ):
            return name
    return None


def tabulate(
    candidates: Sequence[str],
    ballots: Sequence[Ballot],
) -> dict:
    """Run instant-runoff voting and return a round-by-round report."""
    names = list(candidates)
    if not names:
        raise InputError("no candidates to count")
    if len(names) > MAX_CANDIDATES:
        raise InputError(f"at most {MAX_CANDIDATES} candidates are supported")

    ballots = list(ballots)
    if not ballots:
        raise InputError("no ballots to count")

    roster = set(names)
    for ballot in ballots:
        for name in ballot.ranking:
            if name not in roster:
                raise InputError(
                    f"line {ballot.line}: {name!r} is not a declared candidate"
                )

    first_preferences = {name: 0 for name in names}
    for ballot in ballots:
        first_preferences[ballot.ranking[0]] += ballot.weight

    total_weight = sum(ballot.weight for ballot in ballots)

    rounds: list[dict] = []
    remaining = list(names)
    eliminated: set[str] = set()
    winner: Optional[str] = None
    tied: list[str] = []
    reason = ""

    while True:
        tally, active, exhausted = _round_tally(ballots, remaining, eliminated)
        record: dict = {
            "round": len(rounds) + 1,
            "tally": dict(tally),
            "active_votes": active,
            "exhausted": exhausted,
            "majority_needed": active // 2 + 1 if active else 0,
            "eliminated": None,
            "tie_break": [],
            "winner": None,
        }
        rounds.append(record)

        if len(remaining) == 1:
            winner = remaining[0]
            reason = "last_standing"
            record["winner"] = winner
            break

        leaders = [name for name in remaining if tally[name] >= record["majority_needed"]]
        if active and len(leaders) == 1:
            winner = leaders[0]
            reason = "majority"
            record["winner"] = winner
            break

        lowest = min(tally.values())
        last_place = [name for name in remaining if tally[name] == lowest]
        if len(last_place) == len(remaining):
            tied = sorted(last_place)
            reason = "tie"
            record["tie_break"] = list(tied)
            break

        if len(last_place) > 1:
            last_place.sort(key=lambda name: (first_preferences[name], name))
            record["tie_break"] = list(last_place)
        doomed = last_place[0]
        record["eliminated"] = doomed
        remaining.remove(doomed)
        eliminated.add(doomed)

    matrix = pairwise_matrix(names, ballots)
    condorcet = condorcet_winner(names, matrix)

    return {
        "winner": winner,
        "tied": tied,
        "reason": reason,
        "candidates": names,
        "rounds": rounds,
        "ballots": {
            "lines": len(ballots),
            "weight": total_weight,
            "distinct_rankings": len({ballot.ranking for ballot in ballots}),
        },
        "first_preferences": first_preferences,
        "pairwise": matrix,
        "condorcet": condorcet,
        "condorcet_agrees": None if (winner is None or condorcet is None) else winner == condorcet,
    }


def run(text: str, candidates: object = None) -> dict:
    """Parse ``text`` and tabulate it — the one call the API needs."""
    ballots, roster = parse_ballots(text, candidates)
    if not roster and not ballots:
        raise InputError("ballots: nothing to count")
    return tabulate(roster, ballots)
