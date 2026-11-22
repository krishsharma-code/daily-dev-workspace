"""Runnable demo for inr_money: an invoice total, formatted and spelled out.

    python example.py
"""

from decimal import Decimal

from inr_money import (
    MoneyError,
    amount_to_words,
    format_inr,
    group_digits,
    integer_to_words,
    parse_inr,
)

# (description, unit price as text, quantity) — prices arrive as text from a
# form or a CSV, which is exactly where parse_inr earns its keep.
ITEMS = [
    ("Steel water bottle", "Rs. 749", 3),
    ("Notebook (A5, ruled)", "\u20b9 1,29,999.50", 1),
    ("Courier charges", "2.5 lakh", 1),
    ("GST", "12k", 1),
]


def show(title):
    print(f"\n{title}\n{'-' * len(title)}")


def main():
    show("Grouping")
    for digits in ("999", "1000", "100000", "1234567", "1234567890123"):
        print(f"  {digits:>14} -> {group_digits(digits)}")

    show("Invoice")
    total = Decimal(0)
    for description, price, quantity in ITEMS:
        unit = parse_inr(price)
        line = unit * quantity
        total += line
        print(
            f"  {description:<24} {format_inr(unit):>16} x{quantity} "
            f"= {format_inr(line):>18}"
        )
    print(f"  {'TOTAL':<24} {'':>16}    {format_inr(total):>18}")

    show("The same total, three ways")
    print(f"  default          {format_inr(total)}")
    print(f"  no decimals      {format_inr(total, decimals=0)}")
    print(f"  accounting       {format_inr(-total, accounting=True)}")
    print(f"  bare number      {format_inr(total, symbol=None)}")
    print(f"  in words         {amount_to_words(total)}")

    show("Parsing what people actually type")
    for text in ("\u20b91,23,456.78", "Rs 4500", "2.5 lakh", "1.2 cr", "12k", "(1,234.50)"):
        print(f"  {text!r:<22} -> {parse_inr(text)}")

    show("Rounding is half-up, not half-even")
    for amount in ("0.005", "0.015", "1234.5", "1235.5", "-0.004"):
        print(f"  {amount:>9} -> {format_inr(Decimal(amount))}")

    show("Words, Indian scale")
    for number in (0, 21, 100, 12345, 1234567, 12345678, 10**9):
        print(f"  {number:>12} -> {integer_to_words(number)}")

    show("Cheque amounts")
    for amount in ("1", "1234.5", "12345678.90", "0.01", "-500"):
        print(f"  {amount:>12} -> {amount_to_words(amount)}")

    show("Bad input is rejected, never guessed")
    for bad in ("", "abc", "1.2.3", "12 rupees", "\u20b9"):
        try:
            parse_inr(bad)
        except MoneyError as exc:
            print(f"  {bad!r:<14} -> MoneyError: {exc}")


if __name__ == "__main__":
    main()
