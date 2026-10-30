"""Tests for depgraph."""

import pytest

from depgraph import CycleError, Graph, UnknownNodeError


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------
def test_empty_graph():
    g = Graph()
    assert g.nodes == ()
    assert g.edges == ()
    assert len(g) == 0
    assert g.topological_order() == ()
    assert g.layers() == ()
    assert g.critical_path() == ()
    assert g.find_cycle() is None


def test_add_node_is_chainable_and_idempotent():
    g = Graph().add_node("a").add_node("a").add_node("b")
    assert g.nodes == ("a", "b")
    assert g.edges == ()
    assert "a" in g and "z" not in g
    assert len(g) == 2


def test_add_dependency_registers_both_endpoints():
    g = Graph().add_dependency("api", "db")
    assert g.nodes == ("api", "db")
    assert g.dependencies_of("api") == ("db",)
    assert g.dependencies_of("db") == ()
    assert g.dependents_of("db") == ("api",)


def test_duplicate_dependency_does_not_duplicate_edge():
    g = Graph().add_dependency("api", "db").add_dependency("api", "db")
    assert g.edges == (("api", "db"),)
    assert g.dependencies_of("api") == ("db",)


def test_add_dependencies_bulk():
    g = Graph().add_dependencies("deploy", ["test", "build", "lint"])
    assert g.dependencies_of("deploy") == ("build", "lint", "test")


def test_from_mapping_accepts_lists_and_bare_strings():
    g = Graph.from_mapping({"test": "lint", "build": ["lint"], "deploy": []})
    assert g.dependencies_of("test") == ("lint",)
    assert g.dependencies_of("build") == ("lint",)
    assert g.dependencies_of("deploy") == ()
    assert g.nodes == ("build", "deploy", "lint", "test")


def test_from_mapping_registers_leaf_nodes():
    g = Graph.from_mapping({"app": ["lib"]})
    assert "lib" in g
    assert g.dependencies_of("lib") == ()


def test_remove_node_drops_edges_both_ways():
    g = Graph.from_mapping({"a": [], "b": ["a"], "c": ["b"]})
    g.remove_node("b")
    assert g.nodes == ("a", "c")
    assert g.edges == ()
    assert g.dependencies_of("c") == ()


def test_remove_unknown_node_raises():
    with pytest.raises(UnknownNodeError):
        Graph().remove_node("nope")


@pytest.mark.parametrize("bad", [None, 1, 3.5, b"bytes", ("tuple",)])
def test_non_string_node_names_rejected(bad):
    g = Graph()
    with pytest.raises(TypeError):
        g.add_node(bad)
    with pytest.raises(TypeError):
        g.add_dependency("a", bad)


def test_empty_node_name_rejected():
    with pytest.raises(ValueError):
        Graph().add_node("")


def test_unknown_node_lookups_raise():
    g = Graph.from_mapping({"a": []})
    for call in (g.dependencies_of, g.dependents_of, g.impacted_by):
        with pytest.raises(UnknownNodeError) as info:
            call("ghost")
        assert info.value.node == "ghost"
    assert "ghost" in str(UnknownNodeError("ghost"))


# ----------------------------------------------------------------------
# Topological order
# ----------------------------------------------------------------------
def test_topological_order_respects_edges():
    g = Graph.from_mapping({
        "lint": [],
        "test": ["lint"],
        "build": ["lint"],
        "docs": [],
        "deploy": ["test", "build", "docs"],
    })
    order = g.topological_order()
    position = {node: i for i, node in enumerate(order)}
    assert len(order) == len(g)
    for node, dep in g.edges:
        assert position[dep] < position[node]


def test_topological_order_is_deterministic_and_alphabetical():
    g = Graph.from_mapping({"d": [], "b": [], "c": [], "a": []})
    assert g.topological_order() == ("a", "b", "c", "d")
    # Same answer regardless of insertion order.
    h = Graph.from_mapping({"a": [], "c": [], "b": [], "d": []})
    assert h.topological_order() == ("a", "b", "c", "d")


def test_topological_order_prefers_already_ready_nodes():
    # "z" has no dependencies, so it jumps ahead of the chain.
    g = Graph.from_mapping({"z": [], "a": ["b"], "b": []})
    # b is the only ready node; then a becomes ready and sorts before z.
    assert g.topological_order() == ("b", "a", "z")


def test_topological_order_on_chain():
    g = Graph.from_mapping({"c": ["b"], "b": ["a"], "a": []})
    assert g.topological_order() == ("a", "b", "c")


def test_topological_order_on_disconnected_graph():
    g = Graph.from_mapping({"a": [], "b": [], "c": ["a"], "d": ["b"]})
    assert g.topological_order() == ("a", "b", "c", "d")


def test_diamond_graph_order():
    g = Graph.from_mapping({"base": [], "left": ["base"], "right": ["base"], "top": ["left", "right"]})
    assert g.topological_order() == ("base", "left", "right", "top")


# ----------------------------------------------------------------------
# Layers
# ----------------------------------------------------------------------
def test_layers_group_parallel_work():
    g = Graph.from_mapping({
        "lint": [],
        "test": ["lint"],
        "build": ["lint"],
        "deploy": ["test", "build"],
    })
    assert g.layers() == (("lint",), ("build", "test"), ("deploy",))


def test_layers_of_isolated_nodes_is_one_round():
    g = Graph.from_mapping({"a": [], "b": [], "c": []})
    assert g.layers() == (("a", "b", "c"),)


def test_layers_use_deepest_dependency():
    # "top" depends on "a" (depth 0) and "deep" (depth 2), so it lands in 3.
    g = Graph.from_mapping({"a": [], "b": ["a"], "deep": ["b"], "top": ["a", "deep"]})
    assert g.layers() == (("a",), ("b",), ("deep",), ("top",))


def test_every_node_appears_exactly_once_in_layers():
    g = Graph.from_mapping({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": []})
    flat = [node for layer in g.layers() for node in layer]
    assert sorted(flat) == list(g.nodes)
    assert len(flat) == len(set(flat))


# ----------------------------------------------------------------------
# Critical path
# ----------------------------------------------------------------------
def test_critical_path_is_the_longest_chain():
    g = Graph.from_mapping({
        "a": [],
        "b": ["a"],
        "c": ["b"],
        "d": [],
        "top": ["c", "d"],
    })
    assert g.critical_path() == ("a", "b", "c", "top")


def test_critical_path_length_matches_layer_count():
    g = Graph.from_mapping({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]})
    assert len(g.critical_path()) == len(g.layers())


def test_critical_path_tie_break_is_stable():
    g = Graph.from_mapping({"y": [], "x": [], "top": ["y", "x"]})
    # Both roots give a chain of length 2; the alphabetically first wins.
    assert g.critical_path() == ("x", "top")
    assert g.critical_path() == g.critical_path()


def test_critical_path_single_node():
    assert Graph().add_node("only").critical_path() == ("only",)


# ----------------------------------------------------------------------
# Cycles
# ----------------------------------------------------------------------
def test_no_cycle_returns_none():
    assert Graph.from_mapping({"a": [], "b": ["a"]}).find_cycle() is None


def test_self_dependency_is_a_cycle():
    g = Graph().add_dependency("a", "a")
    assert g.find_cycle() == ("a", "a")
    with pytest.raises(CycleError):
        g.topological_order()


def test_two_node_cycle():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"]})
    cycle = g.find_cycle()
    assert cycle[0] == cycle[-1]
    assert set(cycle) == {"a", "b"}


def test_three_node_cycle_is_reported_as_closed_path():
    g = Graph.from_mapping({"web": ["api"], "api": ["db"], "db": ["web"]})
    cycle = g.find_cycle()
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {"web", "api", "db"}
    # Each node in the path depends on the next one.
    for node, dep in zip(cycle, cycle[1:]):
        assert dep in g.dependencies_of(node)


def test_cycle_found_even_with_acyclic_branches():
    g = Graph.from_mapping({
        "root": [],
        "ok": ["root"],
        "x": ["y"],
        "y": ["x"],
    })
    cycle = g.find_cycle()
    assert cycle is not None
    assert set(cycle) == {"x", "y"}


def test_cycle_error_message_and_attribute():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"]})
    with pytest.raises(CycleError) as info:
        g.topological_order()
    error = info.value
    assert isinstance(error, ValueError)
    assert error.cycle[0] == error.cycle[-1]
    assert "dependency cycle detected" in str(error)
    assert " -> " in str(error)


def test_validate_raises_and_passes():
    Graph.from_mapping({"a": [], "b": ["a"]}).validate()
    with pytest.raises(CycleError):
        Graph.from_mapping({"a": ["b"], "b": ["a"]}).validate()


def test_layers_and_critical_path_reject_cycles():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"]})
    with pytest.raises(CycleError):
        g.layers()
    with pytest.raises(CycleError):
        g.critical_path()


def test_long_chain_does_not_hit_recursion_limit():
    # 3000 sequential nodes: a recursive DFS would blow the stack here.
    g = Graph()
    for i in range(3000):
        g.add_dependency(f"n{i}", f"n{i + 1}")
    assert len(g.topological_order()) == 3001
    assert g.find_cycle() is None
    # Close the loop at the far end.
    g.add_dependency("n3000", "n0")
    cycle = g.find_cycle()
    assert cycle is not None
    assert len(cycle) == 3002


# ----------------------------------------------------------------------
# Impact
# ----------------------------------------------------------------------
def test_impacted_by_is_transitive_and_excludes_self():
    g = Graph.from_mapping({
        "base": [],
        "mid": ["base"],
        "top": ["mid"],
        "unrelated": [],
    })
    assert g.impacted_by("base") == ("mid", "top")
    assert g.impacted_by("top") == ()
    assert g.impacted_by("unrelated") == ()


def test_impacted_by_diamond_does_not_duplicate():
    g = Graph.from_mapping({
        "base": [],
        "left": ["base"],
        "right": ["base"],
        "top": ["left", "right"],
    })
    assert g.impacted_by("base") == ("left", "right", "top")


def test_impacted_by_handles_cycle_without_looping():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"]})
    assert g.impacted_by("a") == ("b",)


# ----------------------------------------------------------------------
# Subgraph
# ----------------------------------------------------------------------
def test_subgraph_keeps_only_internal_edges():
    g = Graph.from_mapping({"a": [], "b": ["a"], "c": ["b"]})
    sub = g.subgraph(["b", "c"])
    assert sub.nodes == ("b", "c")
    assert sub.dependencies_of("c") == ("b",)
    assert sub.dependencies_of("b") == ()


def test_subgraph_drops_cycle_exit_edge():
    g = Graph.from_mapping({"a": [], "b": ["a"], "c": ["b"]})
    sub = g.subgraph(["a", "b"])
    assert sub.topological_order() == ("a", "b")


def test_subgraph_rejects_unknown_nodes():
    with pytest.raises(UnknownNodeError):
        Graph.from_mapping({"a": []}).subgraph(["a", "ghost"])


def test_subgraph_is_a_copy():
    g = Graph.from_mapping({"a": [], "b": ["a"]})
    sub = g.subgraph(["a", "b"])
    sub.add_node("c")
    assert "c" not in g


# ----------------------------------------------------------------------
# Realistic end-to-end case
# ----------------------------------------------------------------------
def test_migration_graph_end_to_end():
    g = Graph.from_mapping({
        "create-schema": [],
        "seed-reference-data": ["create-schema"],
        "backfill-orders": ["create-schema"],
        "backfill-users": ["create-schema"],
        "add-index": ["backfill-orders", "backfill-users"],
        "swap-read-path": ["add-index", "seed-reference-data"],
        "drop-legacy-tables": ["swap-read-path"],
    })
    g.validate()
    assert g.layers() == (
        ("create-schema",),
        ("backfill-orders", "backfill-users", "seed-reference-data"),
        ("add-index",),
        ("swap-read-path",),
        ("drop-legacy-tables",),
    )
    assert g.critical_path() == (
        "create-schema",
        "backfill-orders",
        "add-index",
        "swap-read-path",
        "drop-legacy-tables",
    )
    assert g.impacted_by("create-schema") == (
        "add-index",
        "backfill-orders",
        "backfill-users",
        "drop-legacy-tables",
        "seed-reference-data",
        "swap-read-path",
    )
    assert g.impacted_by("drop-legacy-tables") == ()


def test_repr_reports_size():
    g = Graph.from_mapping({"a": [], "b": ["a"]})
    assert repr(g) == "Graph(2 nodes, 1 edges)"


# ----------------------------------------------------------------------
# Input hardening (security boundary)
#
# Node names are interpolated into error messages that land in terminal and
# CI logs, so they are treated as untrusted input: printable, non-blank,
# bounded strings only.
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "bad",
    [
        "",           # empty
        " ",          # single space
        "   \t ",     # whitespace only
        "a\nb",       # newline -> would forge a log line
        "a\rb",       # carriage return
        "\x00",       # NUL
        "deploy\x7f", # DEL
    ],
)
def test_blank_and_control_character_names_are_rejected(bad):
    g = Graph()
    with pytest.raises(ValueError):
        g.add_node(bad)
    with pytest.raises(ValueError):
        g.add_dependency("a", bad)
    with pytest.raises(ValueError):
        g.add_dependency(bad, "a")
    assert g.nodes == ()


def test_names_with_inner_spaces_are_still_allowed():
    # Rejecting control characters must not reject legitimate names.
    g = Graph.from_mapping({"build image": ["install deps"], "install deps": []})
    assert g.topological_order() == ("install deps", "build image")


def test_rejection_message_escapes_the_hostile_name():
    # The error itself must stay a single line even though the input was not.
    with pytest.raises(ValueError) as info:
        Graph().add_node("a\nforged: everything is fine")
    assert "\n" not in str(info.value)
    assert "forged" in str(info.value)  # shown escaped, via repr()


def test_cycle_message_is_single_line():
    g = Graph.from_mapping({"web": ["api"], "api": ["db"], "db": ["web"]})
    with pytest.raises(CycleError) as info:
        g.validate()
    assert "\n" not in str(info.value)
    assert info.value.cycle == ("web", "api", "db", "web")


# ----------------------------------------------------------------------
# add_dependencies: the bare-string footgun and atomic validation
# ----------------------------------------------------------------------
def test_add_dependencies_treats_bare_string_as_one_dependency():
    # Iterating "lint" would otherwise register the nodes l, i, n, t.
    g = Graph().add_dependencies("test", "lint")
    assert g.nodes == ("lint", "test")
    assert g.dependencies_of("test") == ("lint",)
    assert len(g.edges) == 1


def test_add_dependencies_registers_node_when_list_is_empty():
    g = Graph().add_dependencies("deploy", [])
    assert "deploy" in g
    assert g.dependencies_of("deploy") == ()


def test_add_dependencies_validates_node_name_even_with_no_dependencies():
    with pytest.raises(TypeError):
        Graph().add_dependencies(123, [])
    with pytest.raises(ValueError):
        Graph().add_dependencies("", [])


def test_add_dependencies_is_atomic_on_a_bad_dependency():
    g = Graph.from_mapping({"a": []})
    with pytest.raises(TypeError):
        g.add_dependencies("a", ["ok", 7, "never-reached"])
    # Nothing was written, not even the valid prefix.
    assert g.nodes == ("a",)
    assert g.edges == ()


def test_add_dependencies_accepts_any_iterable():
    g = Graph().add_dependencies("deploy", (name for name in ["test", "build"]))
    assert g.dependencies_of("deploy") == ("build", "test")


# ----------------------------------------------------------------------
# Membership and repr under untrusted input / large graphs
# ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, 1, 3.5, ["a"], {"a"}, ("a",)])
def test_contains_returns_false_for_non_names(bad):
    # `in` should answer "no", not raise TypeError on an unhashable argument.
    assert (bad in Graph().add_node("a")) is False


def test_contains_still_finds_real_names():
    g = Graph().add_node("a")
    assert ("a" in g) is True
    assert ("b" in g) is False


def test_repr_counts_edges_without_sorting_them():
    g = Graph.from_mapping({f"n{i}": ["root"] for i in range(50)})
    assert repr(g) == "Graph(51 nodes, 50 edges)"


# ----------------------------------------------------------------------
# Scale: no recursion, no quadratic blow-up on a long chain
# ----------------------------------------------------------------------
def test_deep_chain_does_not_hit_the_recursion_limit():
    size = 3000
    g = Graph.from_mapping(
        {f"n{i:04d}": ([f"n{i - 1:04d}"] if i else []) for i in range(size)}
    )
    assert len(g.topological_order()) == size
    assert g.find_cycle() is None
    assert len(g.layers()) == size
    assert len(g.critical_path()) == size
    assert len(g.impacted_by("n0000")) == size - 1


def test_wide_graph_layers_are_single_round():
    g = Graph.from_mapping({f"n{i}": [] for i in range(500)})
    assert len(g.layers()) == 1
    assert len(g.layers()[0]) == 500


# ----------------------------------------------------------------------
# Cycles: shape of the reported path
# ----------------------------------------------------------------------
def test_self_loop_is_reported_as_a_two_node_cycle():
    g = Graph.from_mapping({"a": ["a"]})
    with pytest.raises(CycleError) as info:
        g.validate()
    assert info.value.cycle == ("a", "a")
    assert g.find_cycle() == ("a", "a")


def test_ordering_methods_propagate_cycle_errors():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"]})
    for call in (g.topological_order, g.layers, g.critical_path):
        with pytest.raises(CycleError):
            call()


def test_impacted_by_excludes_the_node_itself_inside_a_cycle():
    g = Graph.from_mapping({"a": ["b"], "b": ["a"], "c": ["a"]})
    assert g.impacted_by("a") == ("b", "c")


def test_critical_path_tie_break_is_lexicographic():
    # Two equally long chains; the alphabetically smaller one wins.
    g = Graph.from_mapping({"a": [], "z": [], "m1": ["a"], "m2": ["z"], "top": ["m1", "m2"]})
    assert g.critical_path() == ("a", "m1", "top")
