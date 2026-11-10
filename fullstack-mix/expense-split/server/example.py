"""A five-minute tour of the splitting engine, with no server involved.

Run from this folder::

    python server/example.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from splitter import (  # noqa: E402
    balances_for,
    format_amount,
    parse_amount,
    simplify_debts,
    split_equal,
    split_by_weights,
)
from store import ExpenseStore  # noqa: E402


def rule(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def main() -> None:
    rule("1. Amounts are exact integers of paise")
    for text in ("1200", "1200.5", "\u20b91,23,456.78"):
        paise = parse_amount(text)
        print(f"  {text:>14} -> {paise:>10} paise -> {format_amount(paise, symbol=True)}")

    rule("2. An equal split loses nothing")
    shares = split_equal(parse_amount("100"), ["asha", "rahul", "meera"])
    print("  100 across three ->", {k: format_amount(v) for k, v in shares.items()})
    print("  sums back to:", format_amount(sum(shares.values())))

    rule("3. A weighted split (2:1:1) allocates the leftover paise")
    shares = split_by_weights(parse_amount("1000"), {"asha": 2, "rahul": 1, "meera": 1})
    print("  ", {k: format_amount(v) for k, v in shares.items()})

    rule("4. A weekend trip, settled")
    store = ExpenseStore(":memory:")
    store.create_group("Goa Trip", ["asha", "rahul", "meera"])
    store.add_expense("goa-trip", "Beach shack dinner", "asha", "2400")
    store.add_expense(
        "goa-trip", "Cab from airport", "rahul", "900.50",
        split_type="exact",
        shares={"asha": "300.25", "rahul": "300.25", "meera": "300"},
    )
    store.add_expense(
        "goa-trip", "Villa (2 nights)", "meera", "6000",
        split_type="weights",
        weights={"asha": 2, "rahul": 2, "meera": 1},
    )

    for expense in store.list_expenses("goa-trip"):
        print(f"  {expense['description']:<22} {expense['amount']:>9} paid by {expense['payer']}")
    print(f"  {'total':<22} {format_amount(store.settlement('goa-trip')['total_paise']):>9}")

    balances = balances_for(store.list_expenses("goa-trip"), ["asha", "rahul", "meera"])
    print("\n  balances (+ owed to them, - they owe):")
    for name, amount in balances.items():
        print(f"    {name:<8} {format_amount(amount):>10}")

    print("\n  settle up with:")
    for transfer in simplify_debts(balances):
        print(f"    {transfer['from']} pays {transfer['to']} {transfer['amount_display']}")

    store.close()


if __name__ == "__main__":
    main()
