"""Exact money splitting and debt simplification.

Every amount is an integer count of paise (1 rupee = 100 paise). Nothing here
uses floats: ``0.1 + 0.2`` problems do not appear in a ledger, and a split
always sums back to the exact total that was paid.

Three ways to split a bill are supported:

* ``equal``   - divide as evenly as possible; the leftover paise go to the
  first participants in the order given, so the same input always produces the
  same shares.
* ``exact``   - you state each person's share; the shares must add up to the
  amount paid.
* ``weights`` - proportional shares (2:1:1, or percentages), allocated with the
  largest-remainder method so no paise are lost or invented.

``simplify_debts`` then turns a set of net balances into the smallest sensible
list of payments: at most one fewer transfer than there are people involved.
"""

from __future__ import annotations

import re
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

__all__ = [
    "MAX_AMOUNT_PAISE",
    "MINOR_UNITS",
    "Transfer",
    "ValidationError",
    "balances_for",
    "format_amount",
    "parse_amount",
    "simplify_debts",
    "split_by_weights",
    "split_equal",
    "split_exact",
]

MINOR_UNITS = 100  # paise in a rupee
MAX_AMOUNT_PAISE = 10**13  # 1,000 crore rupees - far past any real ledger
MAX_MEMBERS = 50
MAX_NAME = 64

_AMOUNT_RE = re.compile(r"^(\d+)(?:\.(\d{1,2}))?$")
_NAME_STRIP = str.maketrans({"\u20b9": "", ",": "", " ": ""})


class ValidationError(ValueError):
    """Raised when an amount, a name or a split is malformed."""


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------


def parse_amount(value: object) -> int:
    """Parse rupees into integer paise.

    Accepts ``"1200.50"``, ``"1,200.5"``, ``"₹1,200"`` and a plain integer
    number of rupees. Floats are rejected on purpose - a float has already lost
    precision by the time it reaches us, and silently rounding someone's money
    is worse than refusing it.
    """
    if isinstance(value, bool):
        raise ValidationError("amount must be a number or a string, not a boolean")
    if isinstance(value, int):
        paise = value * MINOR_UNITS
    elif isinstance(value, float):
        raise ValidationError(
            "amount must not be a float; send rupees as a string like '1200.50'"
        )
    elif isinstance(value, str):
        cleaned = value.translate(_NAME_STRIP)
        if cleaned[:2].lower() == "rs":
            cleaned = cleaned[2:].lstrip(". ")
        cleaned = cleaned.strip()
        if not cleaned:
            raise ValidationError("amount must not be empty")
        match = _AMOUNT_RE.match(cleaned)
        if not match:
            raise ValidationError(
                "amount must be a positive number with at most two decimals"
            )
        rupees = int(match.group(1))
        decimals = match.group(2) or ""
        paise = rupees * MINOR_UNITS + int(decimals.ljust(2, "0"))
    else:
        raise ValidationError("amount must be a number or a string")

    if paise > MAX_AMOUNT_PAISE:
        raise ValidationError("amount is too large")
    return paise


def format_amount(paise: int, symbol: bool = False) -> str:
    """Format paise using Indian digit grouping: ``1234567 -> "12,345.67"``.

    Indian grouping puts the last three digits together and then groups of two,
    so a crore reads ``1,00,00,000`` rather than ``10,000,000``.
    """
    if isinstance(paise, bool) or not isinstance(paise, int):
        raise ValidationError("paise must be an integer")
    sign = "-" if paise < 0 else ""
    rupees, minor = divmod(abs(paise), MINOR_UNITS)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    body = f"{digits}.{minor:02d}"
    prefix = "\u20b9" if symbol else ""
    return f"{sign}{prefix}{body}"


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------


def normalise_names(names: object, *, what: str = "participants") -> tuple[str, ...]:
    """Validate a participant list: non-empty, unique, bounded."""
    if isinstance(names, (str, bytes)):
        raise ValidationError(f"{what} must be a list of names, not a string")
    if not isinstance(names, (list, tuple)):
        raise ValidationError(f"{what} must be a list of names")
    seen: dict[str, None] = {}
    for name in names:
        if not isinstance(name, str):
            raise ValidationError(f"{what} entries must be strings")
        cleaned = name.strip()
        if not cleaned:
            raise ValidationError(f"{what} entries must not be blank")
        if len(cleaned) > MAX_NAME:
            raise ValidationError(f"{what} entries must be at most {MAX_NAME} characters")
        if cleaned in seen:
            raise ValidationError(f"{what} must not repeat '{cleaned}'")
        seen[cleaned] = None
    if not seen:
        raise ValidationError(f"{what} must not be empty")
    if len(seen) > MAX_MEMBERS:
        raise ValidationError(f"{what} may hold at most {MAX_MEMBERS} names")
    return tuple(seen)


# ---------------------------------------------------------------------------
# Splits
# ---------------------------------------------------------------------------


def _check_total(total: object) -> int:
    if isinstance(total, bool) or not isinstance(total, int):
        raise ValidationError("total must be an integer number of paise")
    if total < 0:
        raise ValidationError("total must not be negative")
    if total > MAX_AMOUNT_PAISE:
        raise ValidationError("total is too large")
    return total


def split_equal(total: int, members: Sequence[str]) -> dict[str, int]:
    """Divide ``total`` as evenly as possible, losing nothing.

    ``100`` paise across three people is ``34, 33, 33``; the odd paise go to the
    participants named first, which keeps the result reproducible.
    """
    total = _check_total(total)
    people = normalise_names(list(members))
    base, leftover = divmod(total, len(people))
    return {
        name: base + (1 if index < leftover else 0)
        for index, name in enumerate(people)
    }


def split_exact(total: int, shares: Mapping[str, object]) -> dict[str, int]:
    """Use stated shares; they must add up to ``total`` exactly."""
    total = _check_total(total)
    if not isinstance(shares, Mapping):
        raise ValidationError("exact splits must map a name to an amount")
    people = normalise_names(list(shares.keys()))
    result = {name: parse_amount(shares[name]) for name in people}
    if sum(result.values()) != total:
        raise ValidationError(
            f"exact shares must add up to {format_amount(total)} "
            f"(they add up to {format_amount(sum(result.values()))})"
        )
    return result


def split_by_weights(total: int, weights: Mapping[str, object]) -> dict[str, int]:
    """Allocate ``total`` in proportion to ``weights``.

    Uses the largest-remainder method: every share is floored, then the paise
    left over go one each to the largest fractional remainders (ties resolved by
    the order the names were given). The shares always sum to ``total``.
    """
    total = _check_total(total)
    if not isinstance(weights, Mapping):
        raise ValidationError("weighted splits must map a name to a weight")
    people = normalise_names(list(weights.keys()))

    parsed: list[tuple[str, Fraction]] = []
    for name in people:
        raw = weights[name]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str, Fraction)):
            raise ValidationError(f"weight for '{name}' must be a number")
        try:
            weight = Fraction(str(raw) if not isinstance(raw, Fraction) else raw)
        except (ValueError, ZeroDivisionError):
            raise ValidationError(f"weight for '{name}' must be a number") from None
        if weight < 0:
            raise ValidationError(f"weight for '{name}' must not be negative")
        parsed.append((name, weight))

    weight_total = sum((weight for _name, weight in parsed), Fraction(0))
    if weight_total <= 0:
        raise ValidationError("weights must add up to more than zero")

    quotas = [(name, Fraction(total) * weight / weight_total) for name, weight in parsed]
    shares = {name: int(quota) for name, quota in quotas}
    leftover = total - sum(shares.values())
    if leftover:
        by_remainder = sorted(
            range(len(quotas)),
            key=lambda index: (-(quotas[index][1] - shares[quotas[index][0]]), index),
        )
        for index in by_remainder[:leftover]:
            shares[quotas[index][0]] += 1
    return shares


def split(
    total: int,
    split_type: str,
    participants: Sequence[str] | None = None,
    shares: Mapping[str, object] | None = None,
    weights: Mapping[str, object] | None = None,
) -> dict[str, int]:
    """Dispatch to the right splitter by name (``equal``/``exact``/``weights``)."""
    kind = (split_type or "equal").strip().lower()
    if kind == "equal":
        return split_equal(total, list(participants or []))
    if kind == "exact":
        return split_exact(total, shares or {})
    if kind in ("weights", "weighted", "percent", "percentage", "shares"):
        return split_by_weights(total, weights or shares or {})
    raise ValidationError("split type must be 'equal', 'exact' or 'weights'")


# ---------------------------------------------------------------------------
# Balances and settlement
# ---------------------------------------------------------------------------


class Transfer(dict):
    """A single payment that moves the group towards settled."""

    def __init__(self, payer: str, payee: str, amount: int) -> None:
        super().__init__(
            **{"from": payer, "to": payee, "amount": amount,
               "amount_display": format_amount(amount)}
        )

    @property
    def payer(self) -> str:
        return self["from"]

    @property
    def payee(self) -> str:
        return self["to"]


def balances_for(
    expenses: Iterable[Mapping[str, object]], members: Sequence[str]
) -> dict[str, int]:
    """Net balance per member, in paise.

    Positive means the group owes them money; negative means they owe the
    group. Every member in ``members`` appears in the result, and the values
    always sum to zero.
    """
    people = normalise_names(list(members), what="members")
    balances = {name: 0 for name in people}
    for expense in expenses:
        payer = expense.get("payer")
        amount = expense.get("amount_paise")
        shares = expense.get("shares") or {}
        if payer not in balances:
            raise ValidationError(f"payer '{payer}' is not in the group")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise ValidationError("expense amount must be a non-negative integer")
        if not isinstance(shares, Mapping):
            raise ValidationError("expense shares must map a name to an amount")
        if sum(shares.values()) != amount:
            raise ValidationError("expense shares must add up to the amount paid")
        balances[payer] += amount
        for name, share in shares.items():
            if name not in balances:
                raise ValidationError(f"'{name}' is not in the group")
            balances[name] -= int(share)
    return balances


def simplify_debts(balances: Mapping[str, int]) -> list[Transfer]:
    """Greedily settle balances with as few transfers as possible.

    Each step pays the largest debtor's debt to the largest creditor; one of the
    two is always cleared, so ``n`` people need at most ``n - 1`` transfers.
    Ties break on the name, so the plan is deterministic.
    """
    if not isinstance(balances, Mapping):
        raise ValidationError("balances must map a name to an amount")
    owed = {}
    for name, amount in balances.items():
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise ValidationError("balances must be integer numbers of paise")
        owed[str(name)] = amount
    if sum(owed.values()) != 0:
        raise ValidationError("balances must add up to zero")

    transfers: list[Transfer] = []
    while True:
        creditors = [(name, amt) for name, amt in owed.items() if amt > 0]
        debtors = [(name, -amt) for name, amt in owed.items() if amt < 0]
        if not creditors or not debtors:
            break
        creditor = min(creditors, key=lambda item: (-item[1], item[0]))
        debtor = min(debtors, key=lambda item: (-item[1], item[0]))
        amount = min(creditor[1], debtor[1])
        transfers.append(Transfer(debtor[0], creditor[0], amount))
        owed[debtor[0]] += amount
        owed[creditor[0]] -= amount
    return transfers
