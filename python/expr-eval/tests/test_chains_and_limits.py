"""Regression tests for chain evaluation and the size/depth boundaries.

Four defects are pinned here. Each one is a case where the module's promise --
"the worst an expression can do is raise :class:`ExprError`" -- did not hold,
or where a perfectly flat expression was refused as though it were nested.

1. A left-associative chain longer than ``MAX_DEPTH`` terms (``1+1+1+...``)
   parsed fine and then failed to evaluate with "expression nests deeper than
   64 levels", a message describing nesting the source never had.
2. A right-recursive chain (``2 ** 2 ** 2 ** ...``) escaped as
   ``RecursionError``, which is not an ``ExprError`` and so is not caught by
   a caller following the documented contract.
3. A float literal that overflows (``1e999``) silently became ``inf``, while
   an equally oversized *integer* literal was correctly a ``ParseError``.
4. Repetition was bounded by the multiplier alone, so ``items * 10_000``
   allocated ``len(items) * 10_000`` elements.
"""

import math

import pytest

import expreval
from expreval import EvalError, ExprError, ParseError, evaluate, parse

# --------------------------------------------------------------------------
# 1. Flat chains are evaluated, not mistaken for nesting
# --------------------------------------------------------------------------


class TestFlatChainsEvaluate:
    def test_long_additive_chain(self):
        source = "+".join(["1"] * 500)
        assert evaluate(source) == 500

    def test_long_additive_chain_beyond_the_node_limit_is_still_refused(self):
        # The node budget, not the depth budget, is what bounds a long chain.
        with pytest.raises(ParseError, match="more than"):
            parse("1" + " + 1" * expreval.MAX_NODES)

    def test_long_multiplicative_chain(self):
        assert evaluate("*".join(["1"] * 300)) == 1

    def test_long_boolean_chain(self):
        assert evaluate(" and ".join(["a"] * 200), {"a": True}) is True
        assert evaluate(" or ".join(["a"] * 200), {"a": False}) is False

    def test_long_subscript_chain(self):
        # A str is subscriptable by index, so the chain stays well-typed.
        assert evaluate("x" + "[0]" * 300, {"x": "abc"}) == "a"

    def test_chain_arithmetic_stays_left_associative(self):
        # 100 - 1 - 1 ... must fold left, not right.
        source = "-".join(["100"] + ["1"] * 50)
        assert evaluate(source) == 50

    def test_chain_division_stays_left_associative(self):
        assert evaluate("100 / 5 / 2") == 10.0

    def test_chain_respects_precedence_across_operators(self):
        assert evaluate("1 + 2 * 3 + 4 * 5 + 6") == 33

    def test_boolean_chain_still_short_circuits(self):
        # ``missing`` must never be resolved once ``a`` is falsy; were it
        # evaluated the call would raise "unknown name" instead.
        assert evaluate("a and missing", {"a": False}) is False
        assert evaluate("a or missing", {"a": True}) is True
        with pytest.raises(EvalError, match="unknown name"):
            evaluate("a or missing", {"a": False})

    def test_boolean_chain_returns_the_operand_not_a_bool(self):
        assert evaluate("0 or 'fallback'") == "fallback"
        assert evaluate("1 and 2 and 3") == 3
        assert evaluate("'x' and 0 and missing") == 0

    def test_mixed_bin_and_bool_chain(self):
        source = " + ".join(["1"] * 50) + " > 10 and " + " and ".join(["a"] * 50)
        assert evaluate(source, {"a": True}) is True


# --------------------------------------------------------------------------
# 2. Right-recursive chains are a ParseError, never a RecursionError
# --------------------------------------------------------------------------


class TestRecursiveChainsRaiseParseError:
    @pytest.mark.parametrize("terms", [200, 400, 600])
    def test_power_chain(self, terms):
        source = "**".join(["2"] * terms)
        with pytest.raises(ParseError, match="nests deeper"):
            parse(source)

    def test_power_chain_does_not_escape_as_recursion_error(self):
        # RecursionError is not an ExprError; catching ExprError must be enough.
        try:
            parse("**".join(["2"] * 900))
        except ExprError:
            pass

    def test_conditional_chain(self):
        source = "1 if a else " * 200 + "0"
        with pytest.raises(ParseError, match="nests deeper"):
            parse(source)

    def test_short_chains_still_parse(self):
        assert evaluate("2 ** 3 ** 2") == 512
        assert evaluate("1 if a else 2 if b else 3", {"a": False, "b": False}) == 3

    def test_genuine_nesting_is_still_refused(self):
        with pytest.raises(ParseError, match="nests deeper"):
            parse("(" * 200 + "1" + ")" * 200)

    def test_depth_guard_is_per_expression_not_cumulative(self):
        # Parsing many shallow expressions must not leak depth into the next.
        for _ in range(200):
            assert parse("1 + (2 * 3)")[0] == "bin"


# --------------------------------------------------------------------------
# 3. A non-finite literal is refused at parse time
# --------------------------------------------------------------------------


class TestNonFiniteLiterals:
    @pytest.mark.parametrize("source", ["1e999", "-1e999", "1e999 + 1", "1e999 > 5"])
    def test_overflowing_float_literal_rejected(self, source):
        with pytest.raises(ParseError, match="not finite"):
            parse(source)

    def test_largest_finite_literal_still_accepted(self):
        assert evaluate("1e308") == 1e308

    def test_arithmetic_overflow_still_yields_infinity(self):
        # A computed overflow is ordinary float behaviour and is unchanged;
        # only a literal written in the source is refused.
        assert math.isinf(evaluate("1e308 * 10"))
        assert math.isnan(evaluate("(1e308 * 10) - (1e308 * 10)"))

    def test_oversized_integer_literal_still_rejected(self):
        with pytest.raises(ParseError):
            parse("1" * 5000)


# --------------------------------------------------------------------------
# 4. Repetition is bounded by total size, not only by the multiplier
# --------------------------------------------------------------------------


class TestRepetitionSizeBound:
    def test_multiplier_bound_unchanged(self):
        with pytest.raises(EvalError, match="exceeds the limit"):
            evaluate("'x' * 1000000")
        with pytest.raises(EvalError, match="exceeds the limit"):
            evaluate("1000000 * [1]")

    def test_total_size_bound(self):
        with pytest.raises(EvalError, match="elements"):
            evaluate("items * 10000", {"items": [0] * 100})
        with pytest.raises(EvalError, match="elements"):
            evaluate("s * 10000", {"s": "abcdef"})

    def test_small_repetitions_still_work(self):
        assert evaluate("'x' * 10000") == "x" * 10000
        assert evaluate("'ab' * 5000") == "ab" * 5000
        assert evaluate("[1, 2] * 3") == [1, 2] * 3
        assert evaluate("2 * [1]") == [1, 1]

    def test_empty_sequence_repeats_freely(self):
        # Nothing is allocated, so a huge count of an empty sequence is fine.
        assert evaluate("x * 10000", {"x": ""}) == ""
