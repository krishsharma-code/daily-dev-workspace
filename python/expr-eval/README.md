# expr-eval

A safe evaluator for small arithmetic and logic expressions — pricing
formulas, feature-flag conditions, alert thresholds, spreadsheet cells. It
parses a Python-shaped subset of the language into a tree and interprets that
tree itself, so nothing in an expression can reach `eval`, the filesystem, or
the network.

```python
from expreval import evaluate

evaluate("2 * (price + tax)", {"price": 250, "tax": 30})        # 560
evaluate("tier == 'pro' and seats > 5", {"tier": "pro", "seats": 9})   # True
evaluate("max(1, x)", {"x": 7})                                  # 7
```

## API

| Function | Purpose |
| --- | --- |
| `evaluate(source, variables=None, functions=None)` | Parse and evaluate in one call. |
| `compile(source)` | Parse once, returning an `Expression` you can evaluate repeatedly. |
| `parse(source)` | The raw tree, if you want to inspect or store it. |
| `tokenize(source)` | Tokens, for error messages and tests. |

`Expression` carries `.source` and `.evaluate(variables=None, functions=None)`.
The tree is never mutated, so one compiled expression is safe to reuse across
calls and to share between threads.

`variables` is any mapping; `functions` merges over `DEFAULT_FUNCTIONS`, so a
name you pass in shadows the default of the same name.

## The language

Everything below is supported, in Python's precedence order — lowest first:

| Level | Forms |
| --- | --- |
| conditional | `a if c else b` |
| or | `or`, `\|\|` |
| and | `and`, `&&` |
| not | `not x`, `!x` |
| comparison | `==` `!=` `<` `<=` `>` `>=` `in` `not in`, chainable |
| additive | `+` `-` |
| multiplicative | `*` `/` `//` `%` |
| unary | `-x`, `+x` |
| power | `**` (right-associative, binds tighter than unary minus) |
| postfix | `f(args)`, `x[i]` |
| primary | numbers, strings, `True` / `False` / `None`, names, `[..]`, `( .. )` |

Names may be dotted — `customer.profile.tier` walks nested mappings — and a
segment that is missing is an error rather than `None`.

Comparisons chain as they do in Python, and each operand is evaluated exactly
once: `1 < f() < 5` calls `f()` once. A chain is true when every adjacent pair
is true, so `1 < 3 < 2` is `False`.

`and` / `or` short-circuit and return the operand itself, not a bool — `0 or
'fallback'` is `'fallback'`. `not` always returns a bool. Truthiness is
Python's.

Operators keep Python's meanings, with the type errors made explicit: `+`
joins two numbers, two strings, or two lists; `*` repeats a string or list by
an integer; arithmetic on a string raises `EvalError` naming both types.

## What is not in the language

Attribute access, calls on anything but a plain function name, assignment,
imports, comprehensions, lambdas, dict literals, slices, f-strings. Each of
those is a `ParseError`, and they are left out for one reason: every one of
them is a way out of the sandbox. `order.get('subtotal')` is rejected because
a method call is an attribute access on a live object; write
`order['subtotal']` instead.

```python
evaluate("__import__('os').system('echo hi')")   # ParseError
evaluate("'x'.upper()")                          # ParseError
evaluate("x = 1")                                # ParseError: assignment is not supported
evaluate("open('/etc/passwd')")                  # EvalError: unknown function 'open'
```

Only `DEFAULT_FUNCTIONS` are callable — `abs`, `all`, `any`, `bool`, `float`,
`int`, `len`, `max`, `min`, `round`, `sorted`, `str`, `sum` — plus whatever
you pass as `functions`. Anything else is `EvalError: unknown function`.

## Errors

`ExprError` is the base; catch it and you have caught everything this module
raises.

| Error | When | Example message |
| --- | --- | --- |
| `ParseError` | the text is not a valid expression | `unexpected character '$' at position 3` |
| `EvalError` | valid syntax, bad value | `unknown name 'order.total'` |
| `TypeError` | wrong Python types at the call site | `variables must be a mapping, not list` |

Messages carry the character position where it is known, and an `EvalError`
names both operand types rather than echoing a bare `TypeError` from deep in
the walk. Nothing is swallowed: an exception raised inside a function you
supplied is re-raised as `EvalError("name() failed: ...")` with the original
message attached.

## Limits

Expressions arrive from config files and from users, so each of these is
bounded rather than trusted. All are module-level constants, changeable at
import time if you need to.

| Limit | Constant | Default | Guards against |
| --- | --- | --- | --- |
| source length | `MAX_SOURCE_LENGTH` | 10 000 chars | a megabyte pasted into a config field |
| tree size | `MAX_NODES` | 1 000 nodes | `1 + 1 + 1 + ...` a million times |
| nesting depth | `MAX_DEPTH` | 64 levels | `((((...))))` and `-----1` chains, which would otherwise surface as `RecursionError` |
| exponent | `MAX_EXPONENT` | 1 000 | `10 ** 100000`, which allocates a huge int |
| repetition | `MAX_REPEAT` | 10 000 | `'x' * 1000000`, which allocates a huge string |

`0 ** 100000` and `1 ** 100000` are still allowed — the result is small, so
only a base larger than 1 in absolute value is refused.

The limits are checked during parsing and evaluation, not by a timer, so a
refused expression costs nothing. What they do *not* cover is a function you
supplied doing something slow or large; that is yours to bound.

## Layout

```
python/expr-eval/
├── README.md
├── expreval.py               # implementation
├── example.py                # runnable demo
├── conftest.py               # makes the module importable in tests
└── tests/
    └── test_expreval.py
```

## Run the demo

```bash
cd python/expr-eval
python example.py
```

It walks a discount formula, a shipping rule and a seat-based discount over one
order, compiles a tax formula once and reuses it at three rates, then shows the
refusals: an unknown variable, a type mismatch, an assignment, an import
attempt, a method call, and a runaway exponent.

## Run the tests

```bash
cd python/expr-eval
python -m pytest
```

85 tests: literals and escapes, operator precedence and associativity (both
`**` and chained comparisons), every operator's type errors, dotted paths and
subscripts, default and caller-supplied functions, parse-error positions, and
the safety limits — including that a 500-long unary chain raises `ParseError`
rather than `RecursionError`. No third-party dependency at runtime; `pytest`
is needed only to run the suite.

## Example

```python
from expreval import EvalError, ParseError, compile

order = {
    "order": {"subtotal": 2400, "items": 3, "country": "IN"},
    "customer": {"tier": "pro", "seats": 9},
}

evaluate = compile(
    "order.subtotal * (1 - discount / 100) "
    "if order.country in ['IN', 'SG'] else order.subtotal"
).evaluate

evaluate({"order": order["order"], "discount": 10})    # 2160.0

try:
    evaluate({"order": order["order"]})                # 'discount' missing
except EvalError as error:
    print(error)                                       # unknown name 'discount'
```

Compile once at startup for anything on a hot path — parsing dominates the
cost of a short expression.
