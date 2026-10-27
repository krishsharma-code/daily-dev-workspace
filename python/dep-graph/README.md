# depgraph

A small, dependency-free dependency graph for build pipelines, migration
scripts, and deployment rollouts. It answers the three questions build tooling
actually asks: **in what order**, **what can run in parallel**, and **what
breaks if this changes**.

## Why another one

The useful part is not the topological sort — that is twenty lines. It is the
guarantees around it:

- **Deterministic.** Ties are broken alphabetically via a heap, so the same
  graph always produces the same order. Set iteration order never leaks into
  your output, which means no flaky CI diffs.
- **Cycles come with a path.** Instead of `"circular dependency"`, you get
  `("web", "api", "db", "web")` — the actual loop, each node depending on the
  next.
- **Impact queries.** `impacted_by("lib")` returns every transitive dependent,
  so "which tests need re-running" has an exact answer.
- **No recursion.** Cycle detection is an iterative DFS, so a 3000-node chain
  will not hit the recursion limit.

No third-party packages. Python 3.9+.

## Run it

```bash
cd python/dep-graph
python example.py        # demo: schedule a release pipeline, then break it
python -m pytest -q      # 48 tests
```

## Use it

```python
from depgraph import CycleError, Graph

graph = Graph.from_mapping({
    "checkout": [],
    "install-deps": ["checkout"],
    "lint": ["install-deps"],
    "unit-tests": ["install-deps"],
    "build-image": ["install-deps"],
    "deploy": ["lint", "unit-tests", "build-image"],
})

graph.topological_order()
# ('checkout', 'install-deps', 'build-image', 'lint', 'unit-tests', 'deploy')

graph.layers()
# (('checkout',), ('install-deps',), ('build-image', 'lint', 'unit-tests'), ('deploy',))

graph.critical_path()
# ('checkout', 'install-deps', 'build-image', 'deploy')

graph.impacted_by("install-deps")
# ('build-image', 'deploy', 'lint', 'unit-tests')
```

`add_dependency("api", "db")` records that *api depends on db*, so `db` comes
first. Both endpoints are registered automatically, so a graph can be built
purely from edges.

## API

| Call | Returns |
| --- | --- |
| `Graph()` | empty graph |
| `Graph.from_mapping({node: deps})` | graph from a dict; a bare string means one dependency |
| `add_node(name)` | `self` (chainable) |
| `add_dependency(node, depends_on)` | `self`; registers both endpoints |
| `add_dependencies(node, iterable)` | `self` |
| `remove_node(name)` | `self`; drops every edge touching the node |
| `.nodes`, `.edges` | sorted tuples |
| `dependencies_of(node)` | direct dependencies, sorted |
| `dependents_of(node)` | direct dependents, sorted |
| `topological_order()` | one valid order, alphabetical tie-break |
| `layers()` | tuple of rounds; nodes in a round are mutually independent |
| `critical_path()` | longest dependency chain — the floor on sequential rounds |
| `find_cycle()` | closed path such as `("a", "b", "a")`, or `None` |
| `validate()` | raises `CycleError` if cyclic, else returns `None` |
| `impacted_by(node)` | all transitive dependents, excluding the node itself |
| `subgraph(names)` | new graph over just those nodes; exit edges dropped |

Node names must be non-empty `str` — that is what makes ordering and messages
deterministic. Anything else raises `TypeError` / `ValueError`. Looking up a
name that was never added raises `UnknownNodeError` (a `KeyError`).
`CycleError` is a `ValueError` and carries the path in `.cycle`.

## Notes on the algorithms

- **Ordering** is Kahn's algorithm with a min-heap of ready nodes, so it is
  O(V + E) with a log factor from the heap.
- **Layers** are computed from the topological order: a node's round is one
  more than the deepest round among its dependencies.
- **Critical path** is a memoised longest-chain walk in reverse topological
  order, again with a lexicographic tie-break so the answer is stable.
- **Cycles** are found by iterative DFS with white/grey/black colouring; a grey
  neighbour is a back edge, and the DFS parents reconstruct the loop.

## Layout

```
python/dep-graph/
├── README.md
├── conftest.py               # puts this folder on sys.path for pytest
├── depgraph.py               # the implementation
├── example.py                # runnable demo
└── tests/
    └── test_depgraph.py
```
