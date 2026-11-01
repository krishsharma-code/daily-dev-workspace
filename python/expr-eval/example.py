"""Runnable demo for expreval: pricing rules, flags, and what gets refused.

    python example.py
"""

from expreval import EvalError, ExprError, compile, evaluate

ORDER = {
    "order": {"subtotal": 2400, "items": 3, "country": "IN", "coupon": "MONSOON"},
    "customer": {"tier": "pro", "seats": 9, "lifetime_value": 45000},
}


def show(title, source, variables=None, functions=None):
    try:
        result = evaluate(source, variables, functions)
    except ExprError as error:
        print(f"{title}\n  {source}\n  -> {type(error).__name__}: {error}\n")
        return None
    print(f"{title}\n  {source}\n  -> {result!r}\n")
    return result


def main():
    # 1. A pricing formula, evaluated against the order it belongs to.
    show(
        "discount percentage",
        "10 if customer.tier == 'pro' else 0",
        ORDER,
    )

    # 2. A shipping rule: a comparison chain plus membership, in one line.
    show(
        "free shipping?",
        "order.subtotal >= 1000 and order.country in ['IN', 'SG', 'AE']",
        ORDER,
    )

    # 3. Variables can be dotted paths, and the arithmetic is ordinary.
    show(
        "total after discount",
        "order.subtotal * (1 - discount / 100) + (0 if free_shipping else 99)",
        {
            "order": ORDER["order"],
            "discount": 10,
            "free_shipping": True,
        },
    )

    # 4. Only the whitelisted functions are callable, plus anything you pass in.
    def volume_discount(seats):
        return 5 if seats >= 5 else 0

    show(
        "seats-based discount",
        "max(volume_discount(customer.seats), 2)",
        ORDER,
        {"volume_discount": volume_discount},
    )

    # 5. Parsing is the expensive half, so compile once and reuse.
    formula = compile("order.subtotal * tax_rate / 100")
    print("compiled once:", formula)
    for rate in (5, 12, 18):
        print(f"  GST {rate:>2}% -> {formula.evaluate({'order': ORDER['order'], 'tax_rate': rate}):.2f}")
    print()

    # 6. Bad expressions are errors you can catch, never a traceback from eval.
    show("unknown variable", "order.total * 2", ORDER)
    show("type mismatch", "'two' - 1", ORDER)
    show("not an expression", "order.subtotal = 10", ORDER)
    show("escape attempt", "__import__('os').system('echo hi')", ORDER)
    show("attribute call", "order.get('subtotal')", ORDER)
    show("runaway exponent", "10 ** 100000", ORDER)

    # 7. A failed evaluation leaves your data alone, and EvalError says why.
    try:
        evaluate("order.subtotal / 0", ORDER)
    except EvalError as error:
        print(f"rejected as expected: {error}")
    print(f"ORDER untouched: subtotal={ORDER['order']['subtotal']}")


if __name__ == "__main__":
    main()
