"""Tests for expreval: tokenising, precedence, evaluation, and safety limits."""

import math

import pytest

import expreval
from expreval import (
    EvalError,
    ExprError,
    ParseError,
    compile,
    evaluate,
    parse,
    tokenize,
)


# --------------------------------------------------------------------------
# Literals and tokenising
# --------------------------------------------------------------------------


def test_integer_and_float_literals():
    assert evaluate("42") == 42
    assert evaluate("-7") == -7
    assert evaluate("3.5") == 3.5
    assert evaluate(".5") == 0.5
    assert evaluate("1.5e3") == 1500.0
    assert evaluate("2E-2") == pytest.approx(0.02)
    assert isinstance(evaluate("2.0"), float)


def test_string_literals_and_escapes():
    assert evaluate("'hi'") == "hi"
    assert evaluate('"hi"') == "hi"
    assert evaluate(r"'a\nb'") == "a\nb"
    assert evaluate(r"'tab\there'") == "tab\there"
    assert evaluate(r"'quote\'inside'") == "quote'inside"
    assert evaluate(r"'back\\slash'") == "back\\slash"
    assert evaluate("'empty'") == "empty"
    assert evaluate("''") == ""


def test_constants():
    assert evaluate("True") is True
    assert evaluate("False") is False
    assert evaluate("None") is None


def test_list_literal():
    assert evaluate("[1, 2, 3]") == [1, 2, 3]
    assert evaluate("[]") == []
    assert evaluate("[1, [2, 3], 'x']") == [1, [2, 3], "x"]
    assert evaluate("[1, 2,]") == [1, 2]  # trailing comma tolerated


def test_tokenize_positions():
    tokens = tokenize("a + 12")
    assert [t.kind for t in tokens] == ["NAME", "OP", "NUMBER", "EOF"]
    assert [t.pos for t in tokens] == [0, 2, 4, 6]


def test_tokenize_prefers_longest_operator():
    kinds = [(t.kind, t.value) for t in tokenize("a ** b // c <= d")]
    assert ("OP", "**") in kinds
    assert ("OP", "//") in kinds
    assert ("OP", "<=") in kinds


def test_tokenize_rejects_unicode_digits():
    # str.isdigit() would accept '٣', and int('٣') quietly returns 3.
    with pytest.raises(ParseError):
        tokenize("٣ + 1")


# --------------------------------------------------------------------------
# Arithmetic and precedence
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("10 - 4 - 3", 3),  # left-associative
        ("2 ** 3 ** 2", 512),  # right-associative
        ("-2 ** 2", -4),  # ** binds tighter than unary minus
        ("(-2) ** 2", 4),
        ("2 ** -1", 0.5),
        ("7 // 2", 3),
        ("7 % 2", 1),
        ("7 / 2", 3.5),
        ("--5", 5),
        ("+5", 5),
        ("1 + 2 > 2", True),
        ("2 * 3 == 6 and 1 < 2", True),
    ],
)
def test_precedence(source, expected):
    assert evaluate(source) == expected


def test_division_by_zero_is_an_eval_error():
    for source in ("1 / 0", "1 // 0", "1 % 0"):
        with pytest.raises(EvalError, match="division by zero"):
            evaluate(source)


def test_arithmetic_on_wrong_types():
    with pytest.raises(EvalError, match="needs numbers"):
        evaluate("'a' - 1")
    with pytest.raises(EvalError, match="needs numbers"):
        evaluate("'a' / 2")
    with pytest.raises(EvalError, match="cannot combine"):
        evaluate("'a' + 1")
    with pytest.raises(EvalError, match="cannot combine"):
        evaluate("[1] + 1")
    with pytest.raises(EvalError, match="needs a number"):
        evaluate("-'a'")


def test_string_and_list_concatenation_and_repetition():
    assert evaluate("'a' + 'b'") == "ab"
    assert evaluate("[1] + [2, 3]") == [1, 2, 3]
    assert evaluate("'ab' * 2") == "abab"
    assert evaluate("2 * [0]") == [0, 0]
    assert evaluate("'x' * 0") == ""


# --------------------------------------------------------------------------
# Comparisons, chaining, membership, booleans
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ("1 < 2", True),
        ("2 <= 2", True),
        ("3 > 4", False),
        ("3 >= 3", True),
        ("1 == 1.0", True),
        ("'a' != 'b'", True),
        ("1 < 2 < 3", True),
        ("1 < 3 < 2", False),  # the second link fails
        ("3 > 2 > 1", True),
        ("1 == 1 == 1", True),
    ],
)
def test_comparisons(source, expected):
    assert evaluate(source) == expected


def test_comparison_chain_evaluates_operands_once():
    calls = []

    def track(value):
        calls.append(value)
        return value

    assert evaluate("track(1) < track(2) < track(3)", functions={"track": track}) is True
    assert calls == [1, 2, 3]


def test_membership():
    assert evaluate("2 in [1, 2, 3]") is True
    assert evaluate("5 not in [1, 2, 3]") is True
    assert evaluate("'ell' in 'hello'") is True
    assert evaluate("'k' in d", {"d": {"k": 1}}) is True
    assert evaluate("'z' not in d", {"d": {"k": 1}}) is True
    with pytest.raises(EvalError, match="cannot search"):
        evaluate("1 in 5")


def test_incomparable_types_raise_eval_error():
    with pytest.raises(EvalError, match="cannot compare"):
        evaluate("'a' < 1")


def test_boolean_operators_short_circuit_and_return_operands():
    assert evaluate("1 and 2") == 2
    assert evaluate("0 and 2") == 0
    assert evaluate("0 or 'fallback'") == "fallback"
    assert evaluate("1 or 2") == 1
    assert evaluate("not 0") is True
    assert evaluate("!True") is False
    assert evaluate("1 && 0") == 0
    assert evaluate("0 || 5") == 5


def test_short_circuit_skips_the_other_side():
    def explode():
        raise AssertionError("should not be called")

    assert evaluate("False and explode()", functions={"explode": explode}) is False
    assert evaluate("True or explode()", functions={"explode": explode}) is True


def test_conditional_expression():
    assert evaluate("'yes' if 1 else 'no'") == "yes"
    assert evaluate("'yes' if 0 else 'no'") == "no"
    assert evaluate("1 if a else 2 if b else 3", {"a": False, "b": True}) == 2


# --------------------------------------------------------------------------
# Names, dotted paths, subscripts
# --------------------------------------------------------------------------


def test_variables():
    assert evaluate("a + b", {"a": 2, "b": 3}) == 5
    assert evaluate("a", {"a": None}) is None


def test_missing_variable():
    with pytest.raises(EvalError, match="unknown name 'a'"):
        evaluate("a + 1")


def test_dotted_paths_into_nested_mappings():
    variables = {"user": {"profile": {"tier": "pro"}, "seats": 9}}
    assert evaluate("user.profile.tier", variables) == "pro"
    assert evaluate("user.seats > 5", variables) is True
    with pytest.raises(EvalError, match="unknown name 'user.profile.plan'"):
        evaluate("user.profile.plan", variables)
    with pytest.raises(EvalError, match="cannot look up"):
        evaluate("user.seats.anything", variables)


def test_subscripts():
    assert evaluate("xs[0]", {"xs": [10, 20]}) == 10
    assert evaluate("xs[-1]", {"xs": [10, 20]}) == 20
    assert evaluate("d['k']", {"d": {"k": 7}}) == 7
    assert evaluate("'hello'[1]") == "e"
    with pytest.raises(EvalError, match="no such key"):
        evaluate("d['missing']", {"d": {}})
    with pytest.raises(EvalError, match="out of range"):
        evaluate("xs[5]", {"xs": [1]})
    with pytest.raises(EvalError, match="must be an integer"):
        evaluate("xs['a']", {"xs": [1]})
    with pytest.raises(EvalError, match="cannot be subscripted"):
        evaluate("x[0]", {"x": 5})


# --------------------------------------------------------------------------
# Functions
# --------------------------------------------------------------------------


def test_default_functions():
    assert evaluate("max(1, 5, 3)") == 5
    assert evaluate("min([4, 2, 8])") == 2
    assert evaluate("len('abcd')") == 4
    assert evaluate("abs(-3)") == 3
    assert evaluate("round(2.6)") == 3
    assert evaluate("sum([1, 2, 3])") == 6
    assert evaluate("int('12') + 1") == 13
    assert evaluate("str(12)") == "12"
    assert evaluate("any([0, 1])") is True
    assert evaluate("all([1, 1])") is True
    assert evaluate("sorted([3, 1, 2])") == [1, 2, 3]
    assert evaluate("bool([])") is False
    assert evaluate("float('2.5')") == 2.5


def test_user_functions_and_shadowing():
    def double(value):
        return value * 2

    assert evaluate("double(21)", functions={"double": double}) == 42
    # A caller-supplied name wins over the default of the same name.
    assert evaluate("len(5)", functions={"len": lambda value: "shadowed"}) == "shadowed"


def test_unknown_function():
    with pytest.raises(EvalError, match="unknown function 'nope'"):
        evaluate("nope(1)")


def test_function_errors_become_eval_errors():
    with pytest.raises(EvalError, match="int\\(\\) failed"):
        evaluate("int('abc')")

    def boom():
        raise RuntimeError("kaboom")

    with pytest.raises(EvalError, match="boom\\(\\) failed: kaboom"):
        evaluate("boom()", functions={"boom": boom})


# --------------------------------------------------------------------------
# Parse errors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "",
        "1 +",
        "* 2",
        "(1 + 2",
        "1 + 2)",
        "[1, 2",
        "'unterminated",
        "a.",
        "1 if 2",
        "x = 1",
        "lambda: 1",
        "a.b(c)",
        "'x'.upper()",
        "not",
    ],
)
def test_invalid_expressions_raise_parse_error(source):
    with pytest.raises(ParseError):
        evaluate(source)


def test_error_messages_carry_positions():
    with pytest.raises(ParseError, match="unexpected character '\\$' at position 3"):
        parse("1 +$ 2")
    with pytest.raises(ParseError, match="unexpected end of expression"):
        parse("1 +")
    with pytest.raises(ParseError, match="assignment is not supported"):
        parse("x = 1")


def test_unknown_escape_is_rejected():
    with pytest.raises(ParseError, match="unknown escape"):
        parse(r"'\q'")


def test_parse_error_is_an_expr_error():
    assert issubclass(ParseError, ExprError)
    assert issubclass(EvalError, ExprError)


def test_non_string_source_is_a_type_error():
    with pytest.raises(TypeError):
        evaluate(123)


def test_variables_must_be_a_mapping():
    with pytest.raises(TypeError, match="variables must be a mapping"):
        evaluate("1", [1, 2])


# --------------------------------------------------------------------------
# Safety: nothing here is eval(), and nothing runs away
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('echo hi')",
        "().__class__",
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "globals()",
        "[x for x in range(3)]",
    ],
)
def test_no_escape_hatches(source):
    with pytest.raises(ExprError):
        evaluate(source)


def test_attribute_access_on_a_value_is_a_parse_error():
    with pytest.raises(ParseError, match="only a plain function name can be called"):
        parse("d.get('k')")


def test_huge_exponent_is_refused():
    with pytest.raises(EvalError, match="exceeds the limit"):
        evaluate("10 ** 100000")
    # Small bases are still fine, since the result stays small.
    assert evaluate("1 ** 100000") == 1
    assert evaluate("0 ** 100000") == 0


def test_huge_repetition_is_refused():
    with pytest.raises(EvalError, match="exceeds the limit"):
        evaluate("'x' * 1000000")
    with pytest.raises(EvalError, match="exceeds the limit"):
        evaluate("1000000 * [1]")


def test_source_length_limit():
    with pytest.raises(ParseError, match="limit is"):
        evaluate("1 + " * 5000 + "1")


def test_nesting_depth_limit():
    with pytest.raises(ParseError, match="nests deeper"):
        evaluate("(" * 200 + "1" + ")" * 200)


def test_node_count_limit():
    with pytest.raises(ParseError, match="more than"):
        evaluate("1" + " + 1" * expreval.MAX_NODES)


def test_unary_chains_are_depth_limited():
    # '-----1' is a recursion chain, not a tree: without a guard the parser
    # would surface RecursionError instead of ParseError.
    with pytest.raises(ParseError, match="nests deeper"):
        parse("-" * 500 + "1")
    with pytest.raises(ParseError, match="nests deeper"):
        parse("not " * 500 + "1")
    assert parse("-" * 10 + "1")[0] == "unary"


# --------------------------------------------------------------------------
# Reuse
# --------------------------------------------------------------------------


def test_expression_is_reusable_and_keeps_its_source():
    expression = compile("a * 2")
    assert expression.source == "a * 2"
    assert "a * 2" in repr(expression)
    assert expression.evaluate({"a": 2}) == 4
    assert expression.evaluate({"a": 21}) == 42


def test_evaluate_without_variables():
    assert evaluate("1 + 1") == 2


def test_infinity_and_nan_flow_through():
    assert math.isinf(evaluate("1e308 * 10"))
    assert math.isnan(evaluate("(1e308 * 10) - (1e308 * 10)"))


def test_realistic_rule():
    variables = {
        "order": {"total": 1200, "items": 3, "country": "IN"},
        "customer": {"tier": "pro", "lifetime_value": 45000},
    }
    rule = (
        "order.total > 1000 and order.country in ['IN', 'SG'] "
        "and (customer.tier == 'pro' or customer.lifetime_value > 10000)"
    )
    assert evaluate(rule, variables) is True
    variables["order"]["country"] = "US"
    assert evaluate(rule, variables) is False
