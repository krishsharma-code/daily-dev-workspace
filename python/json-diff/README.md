# json-diff

Structural diff and patch for JSON-like documents, in two dependency-free
halves:

| Function | Purpose |
| --- | --- |
| `diff(source, target)` | Returns an [RFC 6902](https://datatracker.ietf.org/doc/html/rfc6902) JSON Patch — a list of `add` / `remove` / `replace` operations — that turns `source` into `target`. |
| `apply(document, patch)` | Applies a JSON Patch and returns the result. Understands all six operations, so patches produced elsewhere can be replayed too. |

Paths use [RFC 6901](https://datatracker.ietf.org/doc/html/rfc6901) JSON
Pointer, and the awkward cases are handled for you: keys containing `/` or `~`
are escaped correctly, and `~0` / `~1` are decoded strictly, so a typo raises
instead of silently addressing the wrong member.

## API

**`diff(source, target) -> list[dict]`**

Objects are compared key by key, so only the members that actually changed
appear in the patch. Arrays are compared positionally: shared indices are
diffed element by element, then surplus elements are removed from the tail
(highest index first, so the earlier indices stay valid) or appended. Values
are deep-copied out of `target`, so mutating the patch can't reach back into
the document it was built from.

`diff` emits only `add`, `remove` and `replace`. That keeps it predictable and
means the patch never depends on element identity. It does not try to detect
moves — a reordered array reads as a run of replacements, which is correct but
not minimal. Where patch size really matters, emit a `move` by hand.

**`apply(document, patch, *, in_place=False) -> Any`**

| Operation | Notes |
| --- | --- |
| `add` | On an object, an existing member is replaced (per the RFC). On an array, inserts at the index, with `-` meaning "append". |
| `remove` | Deletes the member or element. Removing the document root is an error. |
| `replace` | Requires the target to exist; `""` replaces the whole document. |
| `move` | Removes then re-adds, so moves inside one array shift the way the RFC specifies. Moving a node into its own child is rejected. |
| `copy` | Deep-copies the source value. `from: ""` copies the whole document, which the RFC allows. |
| `test` | Compares with JSON-strict equality; a mismatch aborts the patch. |

By default the input is deep-copied first, so a patch that fails half-way
leaves the caller's data untouched. Pass `in_place=True` to skip that copy and
mutate the document you handed in — and note that this gives up atomicity:
operations already applied stay applied when a later one fails.

Invalid input raises `PatchError` (unknown op, non-string `op` / `path` /
`from`, missing `value` / `from`, nonexistent path, out-of-range index, failed
`test`, descending into a scalar) or `PointerError` (a `path` / `from` that
isn't a valid pointer). Both name the offending operation by position, e.g.
`operation 1: no such member 'b'`.

## Receiving untrusted patches

A patch is usually produced by someone else, so `apply` validates before it
walks. Three checks exist because the obvious versions of them leak:

| Input | Naive behaviour | Here |
| --- | --- | --- |
| `op` that is a dict | `TypeError: unhashable type` out of a set-membership test | `PatchError: 'op' must be a string` |
| `path` / `from` that is a list or `None` | `TypeError` out of `parse_pointer` | `PatchError: 'path' must be a string` |
| Array index `"٣"` (Arabic-Indic 3) or `"²"` | `str.isdigit()` accepts both: the first silently addresses index 3, the second raises `ValueError` from `int()` | `PatchError: invalid array index` |

Array indices are therefore checked against the RFC 6901 grammar (ASCII `0` or
`[1-9][0-9]*`) rather than `str.isdigit()`. Nothing here guards against a
patch that is simply *large* or deeply nested: the copy and the recursive walk
are proportional to the document, so cap patch size at the boundary where you
accept it (an HTTP body limit, a queue message limit) rather than in here.

## Equality is JSON-strict

`True == 1` and `1 == 1.0` hold in Python but not in JSON, so a document that
flips `1` to `true` — or to `1.0` — is reported as a change rather than
comparing equal. That matters when the patch is the audit trail: a type change
is a real change.

## Layout

```
python/json-diff/
├── README.md
├── jsondiff.py               # implementation
├── example.py                # runnable demo
├── conftest.py               # makes the module importable in tests
└── tests/
    └── test_jsondiff.py
```

## Run the demo

```bash
cd python/json-diff
python example.py
```

It diffs two versions of a service config, prints the patch and how it compares
in size to the whole document, replays it, then shows a hand-written patch
using `move` / `copy` / `test`.

## Run the tests

```bash
cd python/json-diff
python -m pytest
```

57 tests: pointer escaping, every operation (including the failure modes),
immutability, malformed-patch hardening (non-string `op` / `path` / `from`,
non-ASCII indices, non-sequence patches), and a seeded randomised round-trip
that checks `apply(a, diff(a, b)) == b` across 300 generated document pairs. No third-party
dependency at runtime; `pytest` is needed only to run the suite.

## Example

```python
from jsondiff import apply, diff

before = {"replicas": 2, "env": {"LOG_LEVEL": "info"}}
after = {"replicas": 3, "env": {"LOG_LEVEL": "debug"}}

patch = diff(before, after)
# [{'op': 'replace', 'path': '/replicas', 'value': 3},
#  {'op': 'replace', 'path': '/env/LOG_LEVEL', 'value': 'debug'}]

apply(before, patch) == after   # True

# A patch from an untrusted peer is validated, never trusted:
apply({"a": 1}, [{"op": {"op": "add"}, "path": "/b", "value": 2}])
# PatchError: operation 0: 'op' must be a string, not dict

# Patches from elsewhere work too:
apply({"a": [1, 2, 3]}, [
    {"op": "test", "path": "/a/0", "value": 1},
    {"op": "move", "from": "/a/0", "path": "/a/2"},
])   # {'a': [2, 3, 1]}
```

Keys that contain a slash or tilde are escaped in the generated paths, so they
round-trip without special handling:

```python
diff({"a/b": 1}, {"a/b": 2})
# [{'op': 'replace', 'path': '/a~1b', 'value': 2}]
```
