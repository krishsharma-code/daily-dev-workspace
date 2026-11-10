"""Tests for the splitting engine (``server/splitter.py``).

The point of this module is that money never goes missing and every result is
reproducible, so the tests hammer those two properties directly.
"""

import random
from fractions import Fraction

import pytest

from splitter import (
    MAX_AMOUNT_PAISE,
    ValidationError,
    balances_for,
    format_amount,
    parse_amount,
    simplify_debts,
    split,
    split_by_weights,
    split_equal,
    split_exact,
)


class TestParseAmount:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("1200", 120000),
            ("1200.5", 120050),
            ("1200.50", 120050),
            ("0.01", 1),
            ("0", 0),
            ("1,23,456.78", 12345678),
            ("\u20b92400", 240000),
            ("Rs. 99.99", 9999),
            ("  15  ", 1500),
        ],
    )
    def test_accepts_rupee_strings(self, text, expected):
        assert parse_amount(text) == expected

    def test_integers_are_rupees(self):
        assert parse_amount(1200) == 120000

    @pytest.mark.parametrize("bad", ["", "  ", "abc", "12.345", "-5", "1.2.3", "12,3.456"])
    def test_rejects_malformed(self, bad):
        with pytest.raises(ValidationError):
            parse_amount(bad)

    def test_rejects_floats_outright(self):
        # A float has already lost precision; refusing it beats rounding money.
        with pytest.raises(ValidationError):
            parse_amount(12.5)

    def test_rejects_booleans_and_other_types(self):
        for bad in (True, None, [], {}):
            with pytest.raises(ValidationError):
                parse_amount(bad)

    def test_rejects_absurd_amounts(self):
        with pytest.raises(ValidationError):
            parse_amount(str(MAX_AMOUNT_PAISE))


class TestFormatAmount:
    @pytest.mark.parametrize(
        "paise,expected",
        [
            (0, "0.00"),
            (5, "0.05"),
            (120000, "1,200.00"),
            (12345678, "1,23,456.78"),
            (1000000000, "1,00,00,000.00"),
            (-250, "-2.50"),
        ],
    )
    def test_indian_grouping(self, paise, expected):
        assert format_amount(paise) == expected

    def test_symbol_prefix(self):
        assert format_amount(150000, symbol=True) == "\u20b91,500.00"

    def test_rejects_non_integers(self):
        with pytest.raises(ValidationError):
            format_amount(1.5)

    def test_round_trips_through_parse(self):
        for paise in (1, 99, 100, 1234567, 99999999):
            assert parse_amount(format_amount(paise)) == paise


class TestSplitEqual:
    def test_even_division(self):
        assert split_equal(1200, ["a", "b", "c"]) == {"a": 400, "b": 400, "c": 400}

    def test_leftover_goes_to_the_first_named(self):
        assert split_equal(100, ["a", "b", "c"]) == {"a": 34, "b": 33, "c": 33}

    def test_zero_total(self):
        assert split_equal(0, ["a", "b"]) == {"a": 0, "b": 0}

    def test_duplicates_rejected(self):
        with pytest.raises(ValidationError):
            split_equal(100, ["a", "a"])

    def test_empty_list_rejected(self):
        with pytest.raises(ValidationError):
            split_equal(100, [])

    def test_string_instead_of_list_rejected(self):
        with pytest.raises(ValidationError):
            split_equal(100, "asha")

    @pytest.mark.parametrize("total", [-1, 1.5, True])
    def test_bad_totals_rejected(self, total):
        with pytest.raises(ValidationError):
            split_equal(total, ["a", "b"])

    def test_always_sums_exactly(self):
        rng = random.Random(7)
        for _ in range(200):
            total = rng.randint(0, 10**7)
            members = [f"m{i}" for i in range(rng.randint(1, 8))]
            shares = split_equal(total, members)
            assert sum(shares.values()) == total
            assert max(shares.values()) - min(shares.values()) <= 1


class TestSplitExact:
    def test_stated_shares(self):
        shares = split_exact(100000, {"a": "600", "b": "400"})
        assert shares == {"a": 60000, "b": 40000}

    def test_short_or_long_sums_rejected(self):
        with pytest.raises(ValidationError):
            split_exact(100000, {"a": "600", "b": "399.99"})
        with pytest.raises(ValidationError):
            split_exact(100000, {"a": "600", "b": "400.01"})

    def test_allows_a_zero_share(self):
        assert split_exact(50000, {"a": "500", "b": "0"}) == {"a": 50000, "b": 0}

    def test_rejects_negative_share(self):
        with pytest.raises(ValidationError):
            split_exact(100, {"a": "200", "b": "-100"})


class TestSplitByWeights:
    def test_two_to_one_to_one(self):
        assert split_by_weights(1000, {"a": 2, "b": 1, "c": 1}) == {
            "a": 500,
            "b": 250,
            "c": 250,
        }

    def test_percentages(self):
        assert split_by_weights(20000, {"a": 33.3, "b": 33.3, "c": 33.4}) == {
            "a": 6660,
            "b": 6660,
            "c": 6680,
        }

    def test_largest_remainder_takes_the_odd_paise(self):
        # 100 / 3 equal weights: 33.33 each, the last paise goes to the first
        # name by the tie-break rule.
        assert split_by_weights(100, {"a": 1, "b": 1, "c": 1}) == {
            "a": 34,
            "b": 33,
            "c": 33,
        }

    def test_zero_weight_member_pays_nothing(self):
        assert split_by_weights(900, {"a": 1, "b": 0}) == {"a": 900, "b": 0}

    def test_all_zero_weights_rejected(self):
        with pytest.raises(ValidationError):
            split_by_weights(900, {"a": 0, "b": 0})

    def test_negative_weight_rejected(self):
        with pytest.raises(ValidationError):
            split_by_weights(900, {"a": 1, "b": -1})

    def test_non_numeric_weight_rejected(self):
        with pytest.raises(ValidationError):
            split_by_weights(900, {"a": 1, "b": "half"})

    def test_fraction_weights_are_exact(self):
        shares = split_by_weights(100, {"a": Fraction(1, 3), "b": Fraction(2, 3)})
        assert shares == {"a": 33, "b": 67}

    def test_always_sums_exactly(self):
        rng = random.Random(11)
        for _ in range(200):
            total = rng.randint(1, 10**8)
            members = [f"m{i}" for i in range(rng.randint(1, 6))]
            weights = {name: rng.randint(0, 50) for name in members}
            if sum(weights.values()) == 0:
                weights[members[0]] = 1
            shares = split_by_weights(total, weights)
            assert sum(shares.values()) == total
            assert all(value >= 0 for value in shares.values())


class TestSplitDispatch:
    def test_aliases(self):
        assert split(100, "equal", participants=["a", "b"]) == {"a": 50, "b": 50}
        assert split(100, "percent", weights={"a": 1, "b": 1}) == {"a": 50, "b": 50}
        assert split(100, "EXACT", shares={"a": "1"}) == {"a": 100}

    def test_unknown_type(self):
        with pytest.raises(ValidationError):
            split(100, "telepathy", participants=["a"])


class TestBalances:
    def test_payer_is_credited_and_everyone_debited(self):
        expenses = [
            {"payer": "a", "amount_paise": 300, "shares": {"a": 100, "b": 100, "c": 100}}
        ]
        balances = balances_for(expenses, ["a", "b", "c"])
        assert balances == {"a": 200, "b": -100, "c": -100}
        assert sum(balances.values()) == 0

    def test_untouched_member_appears_at_zero(self):
        assert balances_for([], ["a", "b"]) == {"a": 0, "b": 0}

    def test_unknown_payer_rejected(self):
        with pytest.raises(ValidationError):
            balances_for(
                [{"payer": "z", "amount_paise": 100, "shares": {"a": 100}}], ["a"]
            )

    def test_shares_must_match_amount(self):
        with pytest.raises(ValidationError):
            balances_for(
                [{"payer": "a", "amount_paise": 100, "shares": {"a": 99}}], ["a", "b"]
            )


class TestSimplifyDebts:
    def test_simple_chain(self):
        transfers = simplify_debts({"a": 200, "b": -100, "c": -100})
        assert [dict(t) for t in transfers] == [
            {"from": "b", "to": "a", "amount": 100, "amount_display": "1.00"},
            {"from": "c", "to": "a", "amount": 100, "amount_display": "1.00"},
        ]

    def test_settled_group_needs_no_transfers(self):
        assert simplify_debts({"a": 0, "b": 0}) == []

    def test_one_person_cannot_owe_money_to_themselves(self):
        for transfer in simplify_debts({"a": 500, "b": -300, "c": -200}):
            assert transfer["from"] != transfer["to"]

    def test_uses_at_most_n_minus_one_transfers(self):
        rng = random.Random(3)
        for _ in range(100):
            names = [f"m{i}" for i in range(rng.randint(2, 8))]
            raw = [rng.randint(-5000, 5000) for _ in names]
            raw[-1] -= sum(raw)  # force a zero-sum ledger
            balances = dict(zip(names, raw))
            transfers = simplify_debts(balances)
            assert len(transfers) <= len(names) - 1

            settled = dict(balances)
            for transfer in transfers:
                settled[transfer["from"]] += transfer["amount"]
                settled[transfer["to"]] -= transfer["amount"]
            assert all(value == 0 for value in settled.values())
            assert all(transfer["amount"] > 0 for transfer in transfers)

    def test_is_deterministic(self):
        balances = {"a": 150, "b": -150, "c": 0}
        assert simplify_debts(balances) == simplify_debts(dict(balances))

    def test_rejects_ledgers_that_do_not_balance(self):
        with pytest.raises(ValidationError):
            simplify_debts({"a": 100, "b": -50})

    def test_rejects_non_integer_balances(self):
        with pytest.raises(ValidationError):
            simplify_debts({"a": 100.5, "b": -100.5})

    def test_transfer_helpers(self):
        transfer = simplify_debts({"a": 100, "b": -100})[0]
        assert (transfer.payer, transfer.payee) == ("b", "a")
        assert transfer["amount_display"] == "1.00"
