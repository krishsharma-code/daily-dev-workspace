"""Tests for jsondiff: JSON Pointer, patch application, and diff generation."""

import copy
import random

import pytest

from jsondiff import (
    PatchError,
    PointerError,
    apply,
    diff,
    escape_token,
    parse_pointer,
    to_pointer,
    unescape_token,
)


# --------------------------------------------------------------------------
# RFC 6901 -- JSON Pointer
# --------------------------------------------------------------------------


def test_escape_and_unescape_roundtrip():
    for token in ["plain", "a/b", "a~b", "~1", "0", "", "sl/ash~tilde"]:
        assert unescape_token(escape_token(token)) == token


def test_escape_replaces_tilde_before_slash():
    # '~' must be escaped first, otherwise '~1' would come out as '/1'.
    assert escape_token("~1") == "~01"
    assert unescape_token("~01") == "~1"


def test_unescape_rejects_bad_escape():
    with pytest.raises(PointerError):
        unescape_token("a~2b")
    with pytest.raises(PointerError):
        unescape_token("trailing~")


def test_parse_pointer():
    assert parse_pointer("") == []
    assert parse_pointer("/") == [""]
    assert parse_pointer("/a/b") == ["a", "b"]
    assert parse_pointer("/a~1b/c~0d") == ["a/b", "c~d"]
    assert parse_pointer("/0/1") == ["0", "1"]


def test_parse_pointer_rejects_missing_leading_slash():
    with pytest.raises(PointerError):
        parse_pointer("a/b")


def test_to_pointer_escapes_tokens():
    assert to_pointer(()) == ""
    assert to_pointer(["a", "b/c"]) == "/a/b~1c"
    assert to_pointer(["k~ey", 0]) == "/k~0ey/0"


def test_to_pointer_and_parse_pointer_are_inverse():
    tokens = ["a/b", "c~d", "", "0"]
    assert parse_pointer(to_pointer(tokens)) == tokens


# --------------------------------------------------------------------------
# apply -- add / remove / replace
# --------------------------------------------------------------------------


def test_apply_add_to_object_and_array():
    document = {"a": 1, "items": [1, 2]}
    patch = [
        {"op": "add", "path": "/b", "value": {"nested": True}},
        {"op": "add", "path": "/items/1", "value": 9},
        {"op": "add", "path": "/items/-", "value": 3},
    ]
    assert apply(document, patch) == {
        "a": 1,
        "b": {"nested": True},
        "items": [1, 9, 2, 3],
    }


def test_apply_add_over_existing_member_replaces_it():
    assert apply({"a": 1}, [{"op": "add", "path": "/a", "value": 2}]) == {"a": 2}


def test_apply_add_at_index_equal_to_length_appends():
    assert apply({"a": [1]}, [{"op": "add", "path": "/a/1", "value": 2}]) == {
        "a": [1, 2]
    }


def test_apply_remove_from_object_and_array():
    document = {"a": 1, "b": 2, "items": ["x", "y", "z"]}
    patch = [
        {"op": "remove", "path": "/a"},
        {"op": "remove", "path": "/items/0"},
    ]
    assert apply(document, patch) == {"b": 2, "items": ["y", "z"]}


def test_apply_replace_nested_and_root():
    assert apply({"a": {"b": 1}}, [{"op": "replace", "path": "/a/b", "value": 2}]) == {
        "a": {"b": 2}
    }
    assert apply({"a": 1}, [{"op": "replace", "path": "", "value": [1, 2]}]) == [1, 2]


def test_apply_replace_requires_existing_path():
    with pytest.raises(PatchError):
        apply({}, [{"op": "replace", "path": "/missing", "value": 1}])


def test_apply_remove_missing_member_raises():
    with pytest.raises(PatchError):
        apply({"a": 1}, [{"op": "remove", "path": "/b"}])


def test_apply_remove_root_raises():
    with pytest.raises(PatchError):
        apply({"a": 1}, [{"op": "remove", "path": ""}])


def test_apply_rejects_unsupported_op():
    with pytest.raises(PatchError, match="unsupported op"):
        apply({}, [{"op": "merge", "path": "/a", "value": 1}])


def test_apply_rejects_missing_required_fields():
    with pytest.raises(PatchError, match="missing 'value'"):
        apply({}, [{"op": "add", "path": "/a"}])
    with pytest.raises(PatchError, match="missing 'from'"):
        apply({"a": 1}, [{"op": "move", "path": "/b"}])


def test_apply_rejects_bad_indexes_and_descent():
    with pytest.raises(PatchError):
        apply({"a": [1]}, [{"op": "replace", "path": "/a/5", "value": 0}])
    with pytest.raises(PatchError):
        apply({"a": [1]}, [{"op": "replace", "path": "/a/x", "value": 0}])
    with pytest.raises(PatchError):
        apply({"a": [1]}, [{"op": "add", "path": "/a/01", "value": 0}])
    with pytest.raises(PatchError):
        apply({"a": 1}, [{"op": "add", "path": "/a/b", "value": 0}])


def test_apply_error_names_the_failing_operation():
    with pytest.raises(PatchError, match="operation 1"):
        apply({"a": 1}, [{"op": "add", "path": "/b", "value": 2}, {"op": "remove", "path": "/nope"}])


# --------------------------------------------------------------------------
# apply -- move / copy / test
# --------------------------------------------------------------------------


def test_apply_move_between_members():
    document = {"from": {"x": 1}, "keep": True}
    assert apply(document, [{"op": "move", "from": "/from/x", "path": "/to"}]) == {
        "from": {},
        "keep": True,
        "to": 1,
    }


def test_apply_move_within_array_shifts_correctly():
    document = {"a": [1, 2, 3]}
    assert apply(document, [{"op": "move", "from": "/a/0", "path": "/a/2"}]) == {
        "a": [2, 3, 1]
    }


def test_apply_move_into_own_child_is_rejected():
    with pytest.raises(PatchError, match="own child"):
        apply({"a": {"b": 1}}, [{"op": "move", "from": "/a", "path": "/a/c"}])


def test_apply_copy_keeps_source_and_deep_copies():
    document = {"a": {"n": 1}}
    result = apply(document, [{"op": "copy", "from": "/a", "path": "/b"}])
    assert result == {"a": {"n": 1}, "b": {"n": 1}}
    result["b"]["n"] = 2
    assert result["a"]["n"] == 1


def test_apply_test_operation():
    document = {"a": 1}
    assert apply(document, [{"op": "test", "path": "/a", "value": 1}]) == {"a": 1}
    with pytest.raises(PatchError, match="test failed"):
        apply(document, [{"op": "test", "path": "/a", "value": 2}])


def test_apply_test_is_type_strict():
    # True == 1 in Python, but not in JSON.
    with pytest.raises(PatchError):
        apply({"a": 1}, [{"op": "test", "path": "/a", "value": True}])
    with pytest.raises(PatchError):
        apply({"a": 1}, [{"op": "test", "path": "/a", "value": 1.0}])


# --------------------------------------------------------------------------
# Immutability
# --------------------------------------------------------------------------


def test_apply_does_not_mutate_input_by_default():
    document = {"a": {"b": 1}, "items": [1, 2]}
    snapshot = copy.deepcopy(document)
    apply(
        document,
        [
            {"op": "replace", "path": "/a/b", "value": 2},
            {"op": "add", "path": "/items/-", "value": 3},
        ],
    )
    assert document == snapshot


def test_failed_patch_leaves_input_untouched():
    document = {"a": 1}
    with pytest.raises(PatchError):
        apply(
            document,
            [
                {"op": "add", "path": "/b", "value": 2},
                {"op": "remove", "path": "/nope"},
            ],
        )
    assert document == {"a": 1}


def test_apply_in_place_mutates_the_given_document():
    document = {"a": 1}
    result = apply(document, [{"op": "add", "path": "/b", "value": 2}], in_place=True)
    assert result is document
    assert document == {"a": 1, "b": 2}


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------


def test_diff_identical_documents_is_empty():
    document = {"a": [1, {"b": None}], "c": "x"}
    assert diff(document, copy.deepcopy(document)) == []


def test_diff_reports_only_changed_object_members():
    patch = diff({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 20, "d": 4})
    assert patch == [
        {"op": "remove", "path": "/c"},
        {"op": "replace", "path": "/b", "value": 20},
        {"op": "add", "path": "/d", "value": 4},
    ]


def test_diff_nested_object_paths():
    assert diff({"a": {"b": {"c": 1}}}, {"a": {"b": {"c": 2}}}) == [
        {"op": "replace", "path": "/a/b/c", "value": 2}
    ]


def test_diff_array_growth_and_shrinkage():
    assert diff({"a": [1]}, {"a": [1, 2, 3]}) == [
        {"op": "add", "path": "/a/1", "value": 2},
        {"op": "add", "path": "/a/2", "value": 3},
    ]
    assert diff({"a": [1, 2, 3]}, {"a": [1]}) == [
        {"op": "remove", "path": "/a/2"},
        {"op": "remove", "path": "/a/1"},
    ]


def test_diff_type_change_replaces_whole_subtree():
    assert diff({"a": {"b": 1}}, {"a": [1]}) == [
        {"op": "replace", "path": "/a", "value": [1]}
    ]


def test_diff_root_replacement_uses_empty_pointer():
    assert diff({"a": 1}, [1, 2]) == [{"op": "replace", "path": "", "value": [1, 2]}]


def test_diff_distinguishes_bool_from_int():
    assert diff({"a": 1}, {"a": True}) == [
        {"op": "replace", "path": "/a", "value": True}
    ]


def test_diff_escapes_awkward_keys():
    patch = diff({"a/b": 1, "c~d": 2}, {"a/b": 3, "c~d": 2})
    assert patch == [{"op": "replace", "path": "/a~1b", "value": 3}]
    assert apply({"a/b": 1, "c~d": 2}, patch) == {"a/b": 3, "c~d": 2}


def test_diff_deep_copies_values_from_target():
    target = {"a": {"nested": [1]}}
    patch = diff({}, target)
    patch[0]["value"]["nested"].append(2)
    assert target == {"a": {"nested": [1]}}


@pytest.mark.parametrize(
    "source, target",
    [
        ({}, {"a": 1}),
        ({"a": 1}, {}),
        ([1, 2, 3], [1, 3]),
        ([[1], [2, [3]]], [[1], [2, [4]], [5]]),
        ({"a": [{"b": 1}, {"c": 2}]}, {"a": [{"b": 2}], "d": None}),
        ({"": {"": 1}}, {"": {"": 2}}),
        ({"k/1": [1, 2]}, {"k/1": [1, 2, 3], "k~0": True}),
        (None, {"a": None}),
        ({"a": None}, None),
        ("text", {"a": "text"}),
    ],
)
def test_diff_then_apply_roundtrip(source, target):
    patch = diff(source, target)
    assert apply(source, patch) == target


# --------------------------------------------------------------------------
# Randomised round-trip (seeded, so failures are reproducible)
# --------------------------------------------------------------------------


def _random_document(rng, depth=0):
    kind = rng.choice(["int", "str", "bool", "null"] + ([] if depth >= 3 else ["object", "array"]))
    if kind == "int":
        return rng.randint(-3, 3)
    if kind == "str":
        return rng.choice(["", "a", "b/c", "x~y", "key"])
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "null":
        return None
    if kind == "object":
        return {
            rng.choice(["a", "b", "c", "d/e", ""]): _random_document(rng, depth + 1)
            for _ in range(rng.randint(0, 4))
        }
    return [_random_document(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def test_random_documents_roundtrip():
    rng = random.Random(20261025)
    for _ in range(300):
        source = _random_document(rng)
        target = _random_document(rng)
        result = apply(source, diff(source, target))
        assert _same(result, target), (source, target)


def _same(left, right):
    """Type-strict equality, so True/1 or 1/1.0 don't compare equal."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right
