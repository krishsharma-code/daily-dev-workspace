"""Structural diff and patch for JSON-like documents.

Two complementary halves, both dependency-free:

* :func:`diff` compares two documents and returns an RFC 6902 JSON Patch
  (a list of ``add`` / ``remove`` / ``replace`` operations) that turns the
  first into the second.
* :func:`apply` applies an RFC 6902 patch -- including the ``move``, ``copy``
  and ``test`` operations that :func:`diff` never emits, so patches produced
  elsewhere can be applied too.

Paths follow RFC 6901 JSON Pointer, with ``~0`` / ``~1`` escaping handled for
you. Nothing here touches the network or the filesystem, and neither function
mutates its arguments unless you explicitly ask it to.

Patches are frequently received from someone else -- a peer syncing a
document, a client sending a change set -- so :func:`apply` treats every
operation as untrusted input: the operation name, ``path`` and ``from`` are
type-checked before use, array indices must be ASCII digits, and every
malformed patch raises :class:`PatchError` (or :class:`PointerError`) rather
than leaking a ``TypeError`` / ``ValueError`` from deep inside the walk.

    >>> diff({"a": 1}, {"a": 2})
    [{'op': 'replace', 'path': '/a', 'value': 2}]
    >>> apply({"a": 1}, [{"op": "add", "path": "/b", "value": 2}])
    {'a': 1, 'b': 2}
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from typing import Any, Iterable

__all__ = [
    "PatchError",
    "PointerError",
    "apply",
    "diff",
    "escape_token",
    "parse_pointer",
    "to_pointer",
    "unescape_token",
]

#: Operations understood by :func:`apply`.
OPERATIONS = frozenset({"add", "remove", "replace", "move", "copy", "test"})


class PointerError(ValueError):
    """A JSON Pointer string was malformed."""


class PatchError(Exception):
    """A patch operation could not be applied to the document."""


# --------------------------------------------------------------------------
# RFC 6901 -- JSON Pointer
# --------------------------------------------------------------------------


def escape_token(token: str) -> str:
    """Escape one reference token for use inside a JSON Pointer."""
    if not isinstance(token, str):
        raise TypeError(f"pointer tokens must be str, not {type(token).__name__}")
    return token.replace("~", "~0").replace("/", "~1")


def unescape_token(token: str) -> str:
    """Decode one reference token taken from a JSON Pointer.

    Escapes are decoded strictly: ``~`` must be followed by ``0`` or ``1``,
    so a typo such as ``/a~2b`` raises :class:`PointerError` instead of being
    silently passed through.
    """
    if "~" not in token:
        return token

    out: list[str] = []
    index = 0
    length = len(token)
    while index < length:
        char = token[index]
        if char != "~":
            out.append(char)
            index += 1
            continue
        following = token[index + 1 : index + 2]
        if following == "0":
            out.append("~")
        elif following == "1":
            out.append("/")
        else:
            raise PointerError(
                f"invalid escape '~{following}' in reference token {token!r}"
            )
        index += 2
    return "".join(out)


def parse_pointer(pointer: str) -> list[str]:
    """Split a JSON Pointer string into its unescaped reference tokens."""
    if not isinstance(pointer, str):
        raise TypeError(f"pointer must be str, not {type(pointer).__name__}")
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PointerError(f"pointer must be empty or start with '/': {pointer!r}")
    return [unescape_token(part) for part in pointer[1:].split("/")]


def to_pointer(tokens: Iterable[Any]) -> str:
    """Build a JSON Pointer string from a sequence of reference tokens."""
    return "".join("/" + escape_token(str(token)) for token in tokens)


# --------------------------------------------------------------------------
# Value comparison
# --------------------------------------------------------------------------


def _equal(left: Any, right: Any) -> bool:
    """Type-strict JSON equality.

    ``True == 1`` and ``1 == 1.0`` hold in Python but not in JSON, so a
    document that flips a value from ``1`` to ``true`` -- or from ``1`` to
    ``1.0`` -- counts as a change here rather than comparing equal.
    """
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right)
        )
    return left == right


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def diff(source: Any, target: Any) -> list[dict[str, Any]]:
    """Return a JSON Patch turning ``source`` into ``target``.

    Objects are diffed key by key, so only the members that actually changed
    appear in the patch. Arrays are diffed positionally: shared indices are
    compared element by element, then surplus elements are removed from the
    end (highest index first, so earlier indices stay valid) or appended.

    The returned operations are applied in order and are sufficient on their
    own: ``apply(source, diff(source, target))`` equals ``target``.
    """
    operations: list[dict[str, Any]] = []
    _diff(source, target, (), operations)
    return operations


def _diff(source: Any, target: Any, path: tuple[str, ...], ops: list[dict[str, Any]]) -> None:
    if isinstance(source, dict) and isinstance(target, dict):
        for key in source:
            if key not in target:
                ops.append({"op": "remove", "path": to_pointer(path + (key,))})
        for key in target:
            if key in source:
                _diff(source[key], target[key], path + (key,), ops)
            else:
                ops.append(
                    {
                        "op": "add",
                        "path": to_pointer(path + (key,)),
                        "value": copy.deepcopy(target[key]),
                    }
                )
        return

    if isinstance(source, list) and isinstance(target, list):
        shared = min(len(source), len(target))
        for index in range(shared):
            _diff(source[index], target[index], path + (str(index),), ops)
        # Trim from the tail so the remaining indices are still valid.
        for index in range(len(source) - 1, shared - 1, -1):
            ops.append({"op": "remove", "path": to_pointer(path + (str(index),))})
        for index in range(shared, len(target)):
            ops.append(
                {
                    "op": "add",
                    "path": to_pointer(path + (str(index),)),
                    "value": copy.deepcopy(target[index]),
                }
            )
        return

    if not _equal(source, target):
        ops.append({"op": "replace", "path": to_pointer(path), "value": copy.deepcopy(target)})


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def apply(document: Any, patch: Sequence[dict[str, Any]], *, in_place: bool = False) -> Any:
    """Apply a JSON Patch to ``document`` and return the result.

    By default the input document is deep-copied, so a patch that fails
    half-way leaves the caller's data untouched. Pass ``in_place=True`` to
    patch the document you handed in and skip that copy.

    Raises :class:`PatchError` when an operation is malformed, targets a path
    that does not exist, or a ``test`` operation fails; :class:`PointerError`
    when a ``path`` / ``from`` string is not a valid JSON Pointer.

    Failures are all-or-nothing only when ``in_place`` is false: the copy is
    thrown away, so the caller's document is untouched. With ``in_place=True``
    operations already applied stay applied when a later one fails -- wrap the
    call in ``try`` / ``except`` and discard the document, or use the default
    copying mode, if you need atomicity.
    """
    if isinstance(patch, (str, bytes)) or not isinstance(patch, Sequence):
        raise PatchError("patch must be a sequence of operation objects")

    result = document if in_place else copy.deepcopy(document)
    for position, operation in enumerate(patch):
        try:
            result = _apply_one(result, operation)
        except (PatchError, PointerError) as error:
            raise type(error)(f"operation {position}: {error}") from error
    return result


def _apply_one(document: Any, operation: Any) -> Any:
    """Apply a single patch operation to ``document`` and return the result.

    ``document`` is mutated in place where it can be (object members, array
    elements) and returned otherwise, so the caller always uses the return
    value. Every field is validated here before it reaches the pointer walk,
    which is why a hostile patch cannot turn into an unhandled ``TypeError``.
    """
    if not isinstance(operation, dict):
        raise PatchError(f"operation must be an object, not {type(operation).__name__}")

    name = operation.get("op")
    # Check the type before the membership test: a non-hashable ``op`` such as
    # a dict would otherwise raise ``TypeError: unhashable type`` instead of a
    # PatchError, and an int/None would be reported as merely unsupported.
    if not isinstance(name, str):
        raise PatchError(f"'op' must be a string, not {type(name).__name__}")
    if name not in OPERATIONS:
        raise PatchError(f"unsupported op {name!r}")
    if "path" not in operation:
        raise PatchError(f"op {name!r} is missing 'path'")
    path = operation["path"]
    if not isinstance(path, str):
        raise PatchError(f"'path' must be a string, not {type(path).__name__}")
    tokens = parse_pointer(path)

    if name == "test":
        value = _require_value(operation, name)
        actual = _resolve(document, tokens)
        if not _equal(actual, value):
            raise PatchError(f"test failed at {path!r}")
        return document

    if name == "remove":
        if not tokens:
            raise PatchError("cannot remove the document root")
        parent, token = _resolve_parent(document, tokens)
        if isinstance(parent, list):
            del parent[_index(parent, token, lenient=False)]
        else:
            if token not in parent:
                raise PatchError(f"no such member {token!r} at {path!r}")
            del parent[token]
        return document

    if name == "add":
        value = _require_value(operation, name)
        if not tokens:
            return copy.deepcopy(value)
        parent, token = _resolve_parent(document, tokens)
        if isinstance(parent, list):
            if token == "-":
                parent.append(copy.deepcopy(value))
            else:
                parent.insert(_index(parent, token, lenient=True), copy.deepcopy(value))
        else:
            # RFC 6902: adding an existing object member replaces it.
            parent[token] = copy.deepcopy(value)
        return document

    if name == "replace":
        value = _require_value(operation, name)
        if not tokens:
            return copy.deepcopy(value)
        parent, token = _resolve_parent(document, tokens)
        if isinstance(parent, list):
            parent[_index(parent, token, lenient=False)] = copy.deepcopy(value)
        else:
            if token not in parent:
                raise PatchError(f"no such member {token!r} at {path!r}")
            parent[token] = copy.deepcopy(value)
        return document

    if name == "copy":
        # RFC 6902 allows copying the whole document (``from`` is the empty
        # pointer); only ``move`` forbids a root source.
        source_tokens = _require_from(operation, name, allow_root=True)
        value = copy.deepcopy(_resolve(document, source_tokens))
        return _apply_one(document, {"op": "add", "path": path, "value": value})

    # name == "move"
    # ``_require_from`` already rejects a root source (you cannot remove the
    # document root, so you cannot move it either).
    source_tokens = _require_from(operation, name)
    if tokens[: len(source_tokens)] == source_tokens and len(tokens) > len(source_tokens):
        raise PatchError(f"cannot move {operation['from']!r} into its own child {path!r}")
    value = copy.deepcopy(_resolve(document, source_tokens))
    document = _apply_one(document, {"op": "remove", "path": operation["from"]})
    return _apply_one(document, {"op": "add", "path": path, "value": value})


def _require_value(operation: dict[str, Any], name: str) -> Any:
    """Return the ``value`` member every write operation must carry."""
    if "value" not in operation:
        raise PatchError(f"op {name!r} is missing 'value'")
    return operation["value"]


def _require_from(operation: dict[str, Any], name: str, *, allow_root: bool = False) -> list[str]:
    """Return the parsed ``from`` pointer of a ``move`` / ``copy`` operation.

    ``allow_root`` is set for ``copy``, where the empty pointer means "the
    whole document"; ``move`` keeps it false because the root cannot be
    removed. A non-string ``from`` is reported as a malformed operation rather
    than surfacing the ``TypeError`` from :func:`parse_pointer`.
    """
    if "from" not in operation:
        raise PatchError(f"op {name!r} is missing 'from'")
    source = operation["from"]
    if not isinstance(source, str):
        raise PatchError(f"'from' must be a string, not {type(source).__name__}")
    tokens = parse_pointer(source)
    if not tokens and not allow_root:
        raise PatchError(f"op {name!r} needs a non-root 'from'")
    return tokens


def _index(container: list[Any], token: str, *, lenient: bool) -> int:
    """Turn an array reference token into an index.

    ``lenient`` allows an index equal to the array length, which ``add``
    permits (it appends); every other operation needs an existing element.

    RFC 6901 array indices are ASCII ``0`` or ``[1-9][0-9]*``, so the check is
    deliberately stricter than :meth:`str.isdigit`: that accepts other Unicode
    digit families (``"\u0663"`` is 3, so ``/a/\u0663`` would quietly address
    index 3) and superscripts (``"\u00b2"`` passes ``isdigit`` but makes
    ``int()`` raise ``ValueError``). Neither should reach the caller.
    """
    if token == "-":
        raise PatchError("'-' is only valid as the final token of an 'add' path")
    if not token.isascii() or not token.isdigit():
        raise PatchError(f"invalid array index {token!r}")
    if len(token) > 1 and token.startswith("0"):
        raise PatchError(f"invalid array index {token!r}")
    index = int(token)
    limit = len(container) if lenient else len(container) - 1
    if index > limit:
        raise PatchError(f"array index {index} out of range for length {len(container)}")
    return index


def _resolve(document: Any, tokens: list[str]) -> Any:
    current = document
    for token in tokens:
        current = _step(current, token)
    return current


def _resolve_parent(document: Any, tokens: list[str]) -> tuple[Any, str]:
    """Return the container holding the last token, plus that token.

    The container is checked here so that every write operation fails with a
    :class:`PatchError` rather than a ``TypeError`` when the path tries to
    address a member of a scalar.
    """
    parent = _resolve(document, tokens[:-1])
    if not isinstance(parent, (dict, list)):
        raise PatchError(
            f"cannot address a member of {type(parent).__name__} with token {tokens[-1]!r}"
        )
    return parent, tokens[-1]


def _step(current: Any, token: str) -> Any:
    if isinstance(current, dict):
        if token not in current:
            raise PatchError(f"no such member {token!r}")
        return current[token]
    if isinstance(current, list):
        return current[_index(current, token, lenient=False)]
    raise PatchError(
        f"cannot descend into {type(current).__name__} with token {token!r}"
    )
