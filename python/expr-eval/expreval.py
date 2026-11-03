"""Safe evaluation of small arithmetic and logic expressions.

A rule engine, a pricing formula, a feature-flag condition, a spreadsheet
cell -- all of them are "a little bit of arithmetic and comparison written by
someone else".  ``eval`` is the obvious answer and the wrong one: it runs
whatever it is handed.  This module parses a deliberately small expression
language and interprets the resulting tree itself, so the worst an expression
can do is raise :class:`ExprError`.

    >>> evaluate("2 * (price + tax)", {"price": 250, "tax": 30})
    560
    >>> evaluate("tier == 'pro' and seats > 5", {"tier": "pro", "seats": 9})
    True
    >>> evaluate("max(1, x)", {"x": 7})
    7

The language is a Python-shaped subset: the usual arithmetic operators,
comparisons (including chaining, so ``1 < x < 5`` works), ``and`` / ``or`` /
``not``, ``in`` / ``not in``, a conditional ``a if c else b``, string and list
literals, subscripts, and calls to a fixed set of functions.  Anything else --
attribute access, assignment, imports, comprehensions, lambdas -- is a
:class:`ParseError`.

Everything is bounded: source length, tree size, nesting depth, the size of
``**`` exponents and of string/list repetition.  Expressions typically arrive
from a config file or a user, so an expression that would take a minute or a
terabyte is rejected rather than served.

Nesting and chains are different things, and the depth budget only measures
nesting.  ``a + b + c`` folds into a left-leaning tree with no nesting at all,
so the evaluator walks that spine iteratively; a flat 500-term sum therefore
evaluates normally instead of being refused for "nesting".  Right-recursive
chains that *do* nest -- ``2 ** 2 ** 2 ** ...`` and ``a if b else a if b
else ...`` -- are depth-guarded in the parser, because an unguarded recursion
there surfaced as ``RecursionError`` from a source well under the length
limit, and ``RecursionError`` is not an :class:`ExprError`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Callable, NamedTuple

__all__ = [
    "DEFAULT_FUNCTIONS",
    "EvalError",
    "ExprError",
    "Expression",
    "ParseError",
    "compile",
    "evaluate",
    "parse",
]

#: Longest source accepted, in characters.
MAX_SOURCE_LENGTH = 10_000
#: Largest expression tree accepted, in nodes.
MAX_NODES = 1_000
#: Deepest nesting accepted (parentheses, brackets, unary chains).
MAX_DEPTH = 64
#: Largest ``**`` exponent allowed when the base is not 0 or 1.
MAX_EXPONENT = 1_000
#: Largest repetition allowed for ``"x" * n`` and ``[..] * n``.
MAX_REPEAT = 10_000
#: Largest total size, in elements, of a repeated string or list.  Bounding the
#: multiplier alone is not enough: ``items * 10_000`` is within ``MAX_REPEAT``
#: however long ``items`` already is, so a caller-supplied list of a million
#: entries would still allocate a billion.
MAX_REPEAT_SIZE = 10_000


class ExprError(Exception):
    """Base class for every error this module raises."""


class ParseError(ExprError):
    """The source text is not a valid expression."""


class EvalError(ExprError):
    """A valid expression could not be evaluated."""


# --------------------------------------------------------------------------
# Tokeniser
# --------------------------------------------------------------------------


class Token(NamedTuple):
    """One lexical token. ``value`` is the text for numbers, the decoded
    string for string literals, and the operator/keyword itself otherwise."""

    kind: str  # "NUMBER" | "STRING" | "NAME" | "OP" | "EOF"
    value: Any
    pos: int

    def describe(self) -> str:
        if self.kind == "EOF":
            return "end of expression"
        if self.kind == "STRING":
            return "a string literal"
        return repr(self.value)


#: Multi-character operators first: the scanner takes the longest match.
_OPERATORS = (
    "**", "//", "==", "!=", "<=", ">=", "&&", "||",
    "+", "-", "*", "/", "%", "<", ">", "!", "(", ")", "[", "]", ",", ".",
)

_KEYWORDS = frozenset({"and", "or", "not", "in", "if", "else", "True", "False", "None"})

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "0": "\0", "b": "\b", "f": "\f",
    "\\": "\\", "'": "'", '"': '"',
}


def _is_digit(ch: str) -> bool:
    # ASCII only: str.isdigit() also accepts '٣' and '²', which int()/float()
    # then either silently coerce or reject with a bare ValueError.
    return "0" <= ch <= "9"


def _scan_number(source: str, start: int) -> tuple[str, int]:
    """Scan a number literal, returning its text and the index after it."""
    i = start
    n = len(source)
    while i < n and _is_digit(source[i]):
        i += 1
    if i < n and source[i] == ".":
        i += 1
        while i < n and _is_digit(source[i]):
            i += 1
    if i < n and source[i] in "eE":
        j = i + 1
        if j < n and source[j] in "+-":
            j += 1
        if j < n and _is_digit(source[j]):
            while j < n and _is_digit(source[j]):
                j += 1
            i = j
    return source[start:i], i


def _scan_string(source: str, start: int) -> tuple[str, int]:
    """Scan a quoted string, decoding the escapes this language allows."""
    quote = source[start]
    i = start + 1
    n = len(source)
    out: list[str] = []
    while True:
        if i >= n:
            raise ParseError(f"unterminated string starting at position {start}")
        ch = source[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\\":
            if i + 1 >= n:
                raise ParseError(f"unterminated string starting at position {start}")
            escape = source[i + 1]
            if escape not in _ESCAPES:
                raise ParseError(f"unknown escape '\\{escape}' at position {i}")
            out.append(_ESCAPES[escape])
            i += 2
            continue
        out.append(ch)
        i += 1


def tokenize(source: str) -> list[Token]:
    """Split ``source`` into tokens. Exposed for tests and for error messages."""
    if not isinstance(source, str):
        raise TypeError(f"expression must be a str, not {type(source).__name__}")
    if len(source) > MAX_SOURCE_LENGTH:
        raise ParseError(
            f"expression is {len(source)} characters long, "
            f"limit is {MAX_SOURCE_LENGTH}"
        )

    tokens: list[Token] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if _is_digit(ch) or (ch == "." and i + 1 < n and _is_digit(source[i + 1])):
            text, i = _scan_number(source, i)
            tokens.append(Token("NUMBER", text, i - len(text)))
            continue
        if ch in "'\"":
            start = i
            text, i = _scan_string(source, i)
            tokens.append(Token("STRING", text, start))
            continue
        if ch.isalpha() or ch == "_":
            j = i + 1
            while j < n and (source[j].isalnum() or source[j] == "_"):
                j += 1
            tokens.append(Token("NAME", source[i:j], i))
            i = j
            continue
        for op in _OPERATORS:
            if source.startswith(op, i):
                tokens.append(Token("OP", op, i))
                i += len(op)
                break
        else:
            if ch == "=":
                raise ParseError(
                    f"assignment is not supported (position {i}); "
                    f"use '==' to compare"
                )
            raise ParseError(f"unexpected character {ch!r} at position {i}")

    tokens.append(Token("EOF", "", n))
    return tokens


# --------------------------------------------------------------------------
# Parser -- produces a tuple tree, one shape per construct
# --------------------------------------------------------------------------
#
#   ("num", value)                  ("str", value)      ("const", value)
#   ("name", (segment, ...))        ("list", [node, ...])
#   ("index", target, index)        ("call", name, [arg, ...])
#   ("unary", op, node)             ("bin", op, left, right)
#   ("bool", "and" | "or", left, right)
#   ("not", node)                   ("cmp", (op, ...), (node, ...))
#   ("cond", test, then, otherwise)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.nodes = 0
        self.depth = 0

    # -- token helpers ----------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        index = min(self.pos + offset, len(self.tokens) - 1)
        return self.tokens[index]

    def _advance(self) -> Token:
        token = self.tokens[self.pos]
        if token.kind != "EOF":
            self.pos += 1
        return token

    def _at_op(self, *ops: str) -> bool:
        token = self._peek()
        return token.kind == "OP" and token.value in ops

    def _at_name(self, *names: str) -> bool:
        token = self._peek()
        return token.kind == "NAME" and token.value in names

    def _accept_op(self, *ops: str) -> bool:
        if self._at_op(*ops):
            self._advance()
            return True
        return False

    def _accept_name(self, *names: str) -> bool:
        if self._at_name(*names):
            self._advance()
            return True
        return False

    def _expect_op(self, op: str) -> None:
        if not self._accept_op(op):
            token = self._peek()
            raise ParseError(
                f"expected {op!r} but found {token.describe()} at position {token.pos}"
            )

    def _expect_name(self, name: str) -> None:
        if not self._accept_name(name):
            token = self._peek()
            raise ParseError(
                f"expected {name!r} but found {token.describe()} at position {token.pos}"
            )

    def _node(self, node: tuple) -> tuple:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ParseError(f"expression has more than {MAX_NODES} nodes")
        return node

    # -- grammar ----------------------------------------------------------

    def parse(self) -> tuple:
        node = self._expression()
        token = self._peek()
        if token.kind != "EOF":
            raise ParseError(
                f"unexpected {token.describe()} at position {token.pos}"
            )
        return node

    def _guard_depth(self) -> None:
        self.depth += 1
        if self.depth > MAX_DEPTH:
            self.depth -= 1
            raise ParseError(f"expression nests deeper than {MAX_DEPTH} levels")

    def _release_depth(self) -> None:
        self.depth -= 1

    def _expression(self) -> tuple:
        self._guard_depth()
        try:
            return self._conditional()
        finally:
            self._release_depth()

    def _conditional(self) -> tuple:
        node = self._or()
        if self._accept_name("if"):
            test = self._or()
            self._expect_name("else")
            # Guarded like _expression: 'a if b else a if b else ...' is a
            # right-recursive chain, so without the guard a long enough source
            # surfaces as RecursionError instead of ParseError.
            self._guard_depth()
            try:
                otherwise = self._conditional()
            finally:
                self._release_depth()
            return self._node(("cond", test, node, otherwise))
        return node

    def _or(self) -> tuple:
        node = self._and()
        while self._accept_name("or") or self._accept_op("||"):
            node = self._node(("bool", "or", node, self._and()))
        return node

    def _and(self) -> tuple:
        node = self._not()
        while self._accept_name("and") or self._accept_op("&&"):
            node = self._node(("bool", "and", node, self._not()))
        return node

    def _not(self) -> tuple:
        if self._accept_name("not") or self._accept_op("!"):
            # Guarded like _expression: a run of 'not' is unbounded recursion
            # otherwise, and would surface as RecursionError instead.
            self._guard_depth()
            try:
                operand = self._not()
            finally:
                self._release_depth()
            return self._node(("not", operand))
        return self._comparison()

    def _comparison(self) -> tuple:
        first = self._additive()
        ops: list[str] = []
        operands: list[tuple] = [first]
        while True:
            op = self._comparison_op()
            if op is None:
                break
            ops.append(op)
            operands.append(self._additive())
        if not ops:
            return first
        return self._node(("cmp", tuple(ops), tuple(operands)))

    def _comparison_op(self) -> str | None:
        if self._at_op("==", "!=", "<", "<=", ">", ">="):
            return str(self._advance().value)
        if self._at_name("in"):
            self._advance()
            return "in"
        if self._at_name("not") and self._peek(1).kind == "NAME" and self._peek(1).value == "in":
            self._advance()
            self._advance()
            return "not in"
        return None

    def _additive(self) -> tuple:
        node = self._multiplicative()
        while self._at_op("+", "-"):
            op = str(self._advance().value)
            node = self._node(("bin", op, node, self._multiplicative()))
        return node

    def _multiplicative(self) -> tuple:
        node = self._unary()
        while self._at_op("*", "/", "//", "%"):
            op = str(self._advance().value)
            node = self._node(("bin", op, node, self._unary()))
        return node

    def _unary(self) -> tuple:
        if self._at_op("-", "+"):
            op = str(self._advance().value)
            # Same reasoning as _not: '-----1' is a recursion chain, not a tree.
            self._guard_depth()
            try:
                operand = self._unary()
            finally:
                self._release_depth()
            return self._node(("unary", op, operand))
        return self._power()

    def _power(self) -> tuple:
        base = self._postfix()
        if self._accept_op("**"):
            # Right-associative, and binds tighter than unary minus, so
            # -2 ** 2 is -(2 ** 2) = -4, as in Python.
            #
            # Guarded because right-associativity makes '2 ** 2 ** 2 ** ...'
            # a recursion chain: a few hundred terms of it escaped as
            # RecursionError, which is not an ExprError and so not catchable
            # by a caller that follows the documented contract.
            self._guard_depth()
            try:
                exponent = self._unary()
            finally:
                self._release_depth()
            return self._node(("bin", "**", base, exponent))
        return base

    def _postfix(self) -> tuple:
        node = self._primary()
        while True:
            if self._at_op("("):
                token = self._peek()
                if node[0] != "name" or len(node[1]) != 1:
                    raise ParseError(
                        "only a plain function name can be called "
                        f"(position {token.pos})"
                    )
                self._advance()
                args: list[tuple] = []
                if not self._at_op(")"):
                    args.append(self._expression())
                    while self._accept_op(","):
                        args.append(self._expression())
                self._expect_op(")")
                node = self._node(("call", node[1][0], args))
                continue
            if self._accept_op("["):
                index = self._expression()
                self._expect_op("]")
                node = self._node(("index", node, index))
                continue
            return node

    def _primary(self) -> tuple:
        token = self._peek()
        if token.kind == "NUMBER":
            self._advance()
            return self._node(("num", _number_value(token)))
        if token.kind == "STRING":
            self._advance()
            return self._node(("str", token.value))
        if token.kind == "NAME":
            self._advance()
            word = token.value
            if word in ("True", "False", "None"):
                return self._node(("const", {"True": True, "False": False, "None": None}[word]))
            if word in _KEYWORDS:
                raise ParseError(
                    f"unexpected keyword {word!r} at position {token.pos}"
                )
            path = [word]
            while self._accept_op("."):
                segment = self._peek()
                if segment.kind != "NAME":
                    raise ParseError(
                        f"expected a name after '.' but found "
                        f"{segment.describe()} at position {segment.pos}"
                    )
                self._advance()
                path.append(segment.value)
            return self._node(("name", tuple(path)))
        if token.kind == "OP" and token.value == "(":
            self._advance()
            node = self._expression()
            self._expect_op(")")
            return node
        if token.kind == "OP" and token.value == "[":
            self._advance()
            items: list[tuple] = []
            if not self._at_op("]"):
                items.append(self._expression())
                while self._accept_op(","):
                    if self._at_op("]"):
                        break  # tolerate a trailing comma
                    items.append(self._expression())
            self._expect_op("]")
            return self._node(("list", items))
        raise ParseError(f"unexpected {token.describe()} at position {token.pos}")


def _number_value(token: Token) -> int | float:
    text = str(token.value)
    try:
        if "." in text or "e" in text or "E" in text:
            value = float(text)
        else:
            return int(text)
    except ValueError:  # e.g. a literal longer than int()'s digit limit
        raise ParseError(f"invalid number {text!r} at position {token.pos}") from None
    # ``float()`` does not fail on an overflowing literal -- ``1e999`` quietly
    # becomes ``inf`` -- so the non-finite check has to be explicit.  A literal
    # is a constant written in the source, not a computed overflow, and letting
    # it through would hand downstream arithmetic an ``inf`` that no
    # ``MAX_EXPONENT`` guard ever saw.  Overflow of an *evaluation* still
    # yields ``inf`` as ordinary float semantics require.
    if not math.isfinite(value):
        raise ParseError(
            f"number literal {text!r} at position {token.pos} is not finite"
        )
    return value


# --------------------------------------------------------------------------
# Evaluator
# --------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, complex)


def _type_name(value: Any) -> str:
    return type(value).__name__


def _numeric(op: str, left: Any, right: Any) -> Any:
    if not _is_number(left) or not _is_number(right):
        raise EvalError(
            f"operator {op!r} needs numbers, got "
            f"{_type_name(left)} and {_type_name(right)}"
        )
    if op in ("/", "//", "%") and right == 0:
        raise EvalError(f"division by zero in {op!r}")
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        return left / right
    if op == "//":
        return left // right
    return left % right  # "%"


def _add(left: Any, right: Any) -> Any:
    if _is_number(left) and _is_number(right):
        return left + right
    if isinstance(left, str) and isinstance(right, str):
        return left + right
    if isinstance(left, list) and isinstance(right, list):
        return left + right
    raise EvalError(
        f"operator '+' cannot combine {_type_name(left)} and {_type_name(right)}"
    )


def _repeat(op: str, left: Any, right: Any) -> Any:
    sequence, count = (left, right) if isinstance(left, (str, list)) else (right, left)
    if not isinstance(sequence, (str, list)) or not isinstance(count, int) or isinstance(count, bool):
        raise EvalError(
            f"operator {op!r} needs a number and a string or list, got "
            f"{_type_name(left)} and {_type_name(right)}"
        )
    if abs(count) > MAX_REPEAT:
        raise EvalError(f"repetition of {count} exceeds the limit of {MAX_REPEAT}")
    if len(sequence) * abs(count) > MAX_REPEAT_SIZE:
        raise EvalError(
            f"repetition of a {len(sequence)}-element "
            f"{_type_name(sequence)} {count} times exceeds the limit of "
            f"{MAX_REPEAT_SIZE} elements"
        )
    return sequence * count


def _power(left: Any, right: Any) -> Any:
    if not _is_number(left) or not _is_number(right):
        raise EvalError(
            f"operator '**' needs numbers, got "
            f"{_type_name(left)} and {_type_name(right)}"
        )
    if isinstance(right, int) and abs(right) > MAX_EXPONENT and abs(left) > 1:
        raise EvalError(
            f"exponent {right} exceeds the limit of {MAX_EXPONENT}; "
            f"the result would be enormous"
        )
    try:
        return left ** right
    except (OverflowError, ZeroDivisionError) as error:
        raise EvalError(f"operator '**' failed: {error}") from None


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "==":
        return bool(left == right)
    if op == "!=":
        return bool(left != right)
    if op == "in":
        try:
            return bool(left in right)
        except TypeError:
            raise EvalError(
                f"operator 'in' cannot search {_type_name(right)} "
                f"with a {_type_name(left)}"
            ) from None
    if op == "not in":
        return not _compare("in", left, right)
    try:
        if op == "<":
            return bool(left < right)
        if op == "<=":
            return bool(left <= right)
        if op == ">":
            return bool(left > right)
        return bool(left >= right)
    except TypeError:
        raise EvalError(
            f"operator {op!r} cannot compare "
            f"{_type_name(left)} and {_type_name(right)}"
        ) from None


def _resolve(path: tuple[str, ...], variables: Mapping[str, Any]) -> Any:
    current: Any = variables
    for position, segment in enumerate(path):
        if isinstance(current, Mapping):
            if segment not in current:
                raise EvalError(f"unknown name {'.'.join(path)!r}")
            current = current[segment]
            continue
        known = ".".join(path[:position]) or "<root>"
        raise EvalError(
            f"cannot look up {'.'.join(path[position:])!r} on "
            f"{_type_name(current)} (reached via {known!r})"
        )
    return current


def _apply_bin(op: str, left: Any, right: Any) -> Any:
    """Apply a binary operator to two already-evaluated operands."""
    if op == "+":
        return _add(left, right)
    if op == "-":
        return _numeric("-", left, right)
    if op == "*":
        if isinstance(left, (str, list)) or isinstance(right, (str, list)):
            return _repeat("*", left, right)
        return _numeric("*", left, right)
    if op in ("/", "//", "%"):
        return _numeric(op, left, right)
    return _power(left, right)  # "**"


def _subscript(target: Any, index: Any) -> Any:
    """Apply one ``target[index]`` lookup, with an explicit error message."""
    if isinstance(target, Mapping):
        try:
            return target[index]
        except (KeyError, TypeError):
            raise EvalError(f"no such key {index!r}") from None
    if isinstance(target, (list, tuple, str)):
        if not isinstance(index, int) or isinstance(index, bool):
            raise EvalError(
                f"subscript of {_type_name(target)} must be an integer, "
                f"not {_type_name(index)}"
            )
        try:
            return target[index]
        except IndexError:
            raise EvalError(
                f"index {index} is out of range for a "
                f"{_type_name(target)} of length {len(target)}"
            ) from None
    raise EvalError(f"{_type_name(target)} cannot be subscripted")


def _eval_chain(
    node: tuple,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]],
    depth: int,
) -> Any:
    """Evaluate a left-associative ``bin``/``bool`` chain without recursing.

    The parser folds ``a + b + c`` into a left-leaning tree, so evaluating it
    one level at a time would recurse once per term and a flat 70-term sum
    would trip the depth guard with a message about nesting the source never
    had.  Walking the left spine iteratively costs one list of the same size
    and keeps ``MAX_DEPTH`` for what it is meant to measure: genuine nesting.

    Short-circuiting is preserved -- ``and``/``or`` still stop at the first
    operand that settles the result, and still return that operand.
    """
    spine: list[tuple[str, str, tuple]] = []
    while node[0] in ("bin", "bool"):
        spine.append((node[0], node[1], node[3]))
        node = node[2]
    value = _eval(node, variables, functions, depth + 1)
    for kind, op, right_node in reversed(spine):
        if kind == "bin":
            value = _apply_bin(
                op, value, _eval(right_node, variables, functions, depth + 1)
            )
        elif op == "and":
            if not value:
                return value
            value = _eval(right_node, variables, functions, depth + 1)
        else:  # "or"
            if value:
                return value
            value = _eval(right_node, variables, functions, depth + 1)
    return value


def _eval_subscripts(
    node: tuple,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]],
    depth: int,
) -> Any:
    """Evaluate ``x[a][b][c]`` iteratively, for the same reason as ``_eval_chain``."""
    spine: list[tuple] = []
    while node[0] == "index":
        spine.append(node[2])
        node = node[1]
    value = _eval(node, variables, functions, depth + 1)
    for index_node in reversed(spine):
        value = _subscript(
            value, _eval(index_node, variables, functions, depth + 1)
        )
    return value


def _eval(
    node: tuple,
    variables: Mapping[str, Any],
    functions: Mapping[str, Callable[..., Any]],
    depth: int,
) -> Any:
    if depth > MAX_DEPTH:
        raise EvalError(f"expression nests deeper than {MAX_DEPTH} levels")

    kind = node[0]

    if kind in ("num", "str", "const"):
        return node[1]

    if kind == "name":
        return _resolve(node[1], variables)

    if kind == "list":
        return [_eval(item, variables, functions, depth + 1) for item in node[1]]

    if kind == "index":
        return _eval_subscripts(node, variables, functions, depth)

    if kind in ("bin", "bool"):
        return _eval_chain(node, variables, functions, depth)

    if kind == "call":
        name, arg_nodes = node[1], node[2]
        function = functions.get(name)
        if function is None:
            raise EvalError(f"unknown function {name!r}")
        args = [_eval(arg, variables, functions, depth + 1) for arg in arg_nodes]
        try:
            return function(*args)
        except ExprError:
            raise
        except Exception as error:  # noqa: BLE001 - surface it as an EvalError
            raise EvalError(f"{name}() failed: {error}") from None

    if kind == "unary":
        op, operand = node[1], _eval(node[2], variables, functions, depth + 1)
        if not _is_number(operand):
            raise EvalError(
                f"unary {op!r} needs a number, got {_type_name(operand)}"
            )
        return -operand if op == "-" else +operand

    if kind == "cmp":
        ops, operands = node[1], node[2]
        left = _eval(operands[0], variables, functions, depth + 1)
        for op, operand_node in zip(ops, operands[1:]):
            right = _eval(operand_node, variables, functions, depth + 1)
            if not _compare(op, left, right):
                return False
            left = right
        return True

    if kind == "not":
        return not _eval(node[1], variables, functions, depth + 1)

    if kind == "cond":
        if _eval(node[1], variables, functions, depth + 1):
            return _eval(node[2], variables, functions, depth + 1)
        return _eval(node[3], variables, functions, depth + 1)

    raise EvalError(f"cannot evaluate node {kind!r}")  # pragma: no cover


#: Functions an expression may call without being granted anything else.
#: Passing ``functions=`` merges over these, so a name can be shadowed.
DEFAULT_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "sorted": sorted,
    "str": str,
    "sum": sum,
}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


class Expression:
    """A parsed expression, ready to be evaluated many times."""

    __slots__ = ("_ast", "_source")

    def __init__(self, ast: tuple, source: str) -> None:
        self._ast = ast
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    def evaluate(
        self,
        variables: Mapping[str, Any] | None = None,
        functions: Mapping[str, Callable[..., Any]] | None = None,
    ) -> Any:
        """Evaluate against ``variables``. The tree is never mutated, so one
        :class:`Expression` is safe to reuse and to share between threads."""
        if variables is None:
            variables = {}
        if not isinstance(variables, Mapping):
            raise TypeError(
                f"variables must be a mapping, not {type(variables).__name__}"
            )
        merged = dict(DEFAULT_FUNCTIONS)
        if functions is not None:
            if not isinstance(functions, Mapping):
                raise TypeError(
                    f"functions must be a mapping, not {type(functions).__name__}"
                )
            merged.update(functions)
        return _eval(self._ast, variables, merged, 0)

    def __repr__(self) -> str:
        return f"Expression({self._source!r})"


def parse(source: str) -> tuple:
    """Parse ``source`` into a tree, raising :class:`ParseError` if it is not
    a valid expression."""
    return _Parser(tokenize(source)).parse()


def compile(source: str) -> Expression:  # noqa: A001 - shadows the builtin on purpose
    """Parse ``source`` once, returning a reusable :class:`Expression`."""
    return Expression(parse(source), source)


def evaluate(
    source: str,
    variables: Mapping[str, Any] | None = None,
    functions: Mapping[str, Callable[..., Any]] | None = None,
) -> Any:
    """Parse and evaluate ``source`` in one call.

    >>> evaluate("a * b + 1", {"a": 3, "b": 4})
    13
    """
    return compile(source).evaluate(variables, functions)
