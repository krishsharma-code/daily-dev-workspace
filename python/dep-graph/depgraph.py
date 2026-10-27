"""Deterministic dependency graph for build, migration, and rollout ordering.

A dependency graph answers three questions that come up constantly in
build tooling and deployment scripts:

1. *In what order can I do this work?* -- ``topological_order()`` gives one
   valid sequence, and it is the same sequence on every run because ties are
   broken alphabetically.
2. *What can run at the same time?* -- ``layers()`` groups the work so that
   every item in a group is independent of its siblings. That is the shape a
   CI pipeline or a thread pool actually wants.
3. *What breaks if I change this?* -- ``impacted_by()`` returns every
   transitive dependent, so a "which tests do I need to re-run" question has
   an exact answer instead of a guess.

Cycle handling is a first-class concern rather than an afterthought: instead
of a bare "circular dependency" error, ``find_cycle()`` returns the offending
path (``("a", "b", "c", "a")``) so the message points at the actual loop.

Node names must be non-empty strings. That restriction is deliberate -- it is
what makes the tie-breaking, sorting, and error messages deterministic.

    from depgraph import Graph, CycleError

    g = Graph.from_mapping({
        "lint": [],
        "test": ["lint"],
        "build": ["lint"],
        "deploy": ["test", "build"],
    })
    g.layers()          # (('lint',), ('build', 'test'), ('deploy',))
    g.critical_path()   # ('lint', 'test', 'deploy')

No third-party dependencies; Python 3.9+.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterable, Mapping

__all__ = ["CycleError", "Graph", "UnknownNodeError"]


def _check_name(name: object, what: str = "node") -> str:
    """Validate a node name, returning it typed as ``str``."""
    if not isinstance(name, str):
        raise TypeError(f"{what} name must be a str, got {type(name).__name__}")
    if not name:
        raise ValueError(f"{what} name must not be empty")
    return name


class UnknownNodeError(KeyError):
    """Raised when a node name was never added to the graph."""

    def __init__(self, node: str) -> None:
        super().__init__(node)
        self.node = node

    def __str__(self) -> str:  # KeyError's default repr adds quotes we don't want
        return f"unknown node: {self.node!r}"


class CycleError(ValueError):
    """Raised when the graph contains a dependency cycle.

    ``cycle`` holds the offending path, first node repeated at the end, and
    each node depends on the one after it::

        CycleError(("web", "api", "db", "web")).cycle
        # ('web', 'api', 'db', 'web')  ->  web depends on api depends on db
        #                                  depends on web
    """

    def __init__(self, cycle: Iterable[str]) -> None:
        self.cycle: tuple[str, ...] = tuple(cycle)
        path = " -> ".join(self.cycle) if self.cycle else "<unknown>"
        super().__init__(f"dependency cycle detected: {path}")

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else "dependency cycle detected"


class Graph:
    """A directed graph where an edge points from a node to what it needs.

    ``add_dependency("api", "db")`` records that *api* depends on *db*, so
    *db* must be built first. Both endpoints are registered automatically,
    which keeps graph construction terse.
    """

    def __init__(self) -> None:
        self._deps: dict[str, set[str]] = {}
        self._dependents: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    @classmethod
    def from_mapping(cls, mapping: Mapping[str, Iterable[str] | str]) -> Graph:
        """Build a graph from ``{node: dependencies}``.

        A bare string value is treated as a single dependency, so
        ``{"test": "lint"}`` and ``{"test": ["lint"]}`` mean the same thing.
        """
        graph = cls()
        for node, deps in mapping.items():
            graph.add_node(node)
            if isinstance(deps, str):
                graph.add_dependency(node, deps)
            else:
                graph.add_dependencies(node, deps)
        return graph

    def add_node(self, name: str) -> Graph:
        """Register an isolated node. Returns ``self`` so calls can chain."""
        name = _check_name(name)
        self._deps.setdefault(name, set())
        self._dependents.setdefault(name, set())
        return self

    def add_dependency(self, node: str, depends_on: str) -> Graph:
        """Record that ``node`` needs ``depends_on`` finished first."""
        node = _check_name(node)
        depends_on = _check_name(depends_on, "dependency")
        self.add_node(node)
        self.add_node(depends_on)
        self._deps[node].add(depends_on)
        self._dependents[depends_on].add(node)
        return self

    def add_dependencies(self, node: str, depends_on: Iterable[str]) -> Graph:
        """Record several dependencies of one node at once."""
        for dep in depends_on:
            self.add_dependency(node, dep)
        return self

    def remove_node(self, name: str) -> Graph:
        """Remove a node and every edge touching it."""
        name = _check_name(name)
        if name not in self._deps:
            raise UnknownNodeError(name)
        for dep in self._deps.pop(name):
            self._dependents[dep].discard(name)
        for dependent in self._dependents.pop(name):
            self._deps[dependent].discard(name)
        return self

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------
    @property
    def nodes(self) -> tuple[str, ...]:
        """Every node name, sorted."""
        return tuple(sorted(self._deps))

    @property
    def edges(self) -> tuple[tuple[str, str], ...]:
        """Every ``(node, dependency)`` pair, sorted."""
        return tuple(
            (node, dep) for node in sorted(self._deps) for dep in sorted(self._deps[node])
        )

    def dependencies_of(self, node: str) -> tuple[str, ...]:
        """Direct dependencies of ``node``, sorted."""
        self._require(node)
        return tuple(sorted(self._deps[node]))

    def dependents_of(self, node: str) -> tuple[str, ...]:
        """Nodes that directly depend on ``node``, sorted."""
        self._require(node)
        return tuple(sorted(self._dependents[node]))

    def __len__(self) -> int:
        return len(self._deps)

    def __contains__(self, node: object) -> bool:
        return node in self._deps

    def __repr__(self) -> str:
        return f"Graph({len(self._deps)} nodes, {len(self.edges)} edges)"

    def _require(self, node: object) -> str:
        node = _check_name(node)
        if node not in self._deps:
            raise UnknownNodeError(node)
        return node

    # ------------------------------------------------------------------
    # Ordering
    # ------------------------------------------------------------------
    def topological_order(self) -> tuple[str, ...]:
        """Return one valid execution order, ties broken alphabetically.

        Uses Kahn's algorithm with a heap, so among all nodes whose
        dependencies are already satisfied the alphabetically first one is
        always taken. Same graph in, same order out -- no reliance on set
        iteration order.

        Raises ``CycleError`` if the graph cannot be linearised.
        """
        remaining = {node: len(deps) for node, deps in self._deps.items()}
        ready = [node for node, count in remaining.items() if count == 0]
        heapq.heapify(ready)

        order: list[str] = []
        while ready:
            node = heapq.heappop(ready)
            order.append(node)
            for dependent in self._dependents[node]:
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    heapq.heappush(ready, dependent)

        if len(order) != len(self._deps):
            # The nodes left over are exactly the ones inside cycles.
            raise CycleError(self.find_cycle() or ())
        return tuple(order)

    def layers(self) -> tuple[tuple[str, ...], ...]:
        """Group nodes into rounds that can each run in parallel.

        A node's layer is one more than the deepest layer among its
        dependencies, so every edge points from a later layer to an earlier
        one. Nodes in the same layer never depend on each other.
        """
        order = self.topological_order()
        depth: dict[str, int] = {}
        for node in order:
            deps = self._deps[node]
            depth[node] = 0 if not deps else 1 + max(depth[dep] for dep in deps)

        if not order:
            return ()
        buckets: list[list[str]] = [[] for _ in range(max(depth.values()) + 1)]
        for node in order:
            buckets[depth[node]].append(node)
        return tuple(tuple(sorted(bucket)) for bucket in buckets)

    def critical_path(self) -> tuple[str, ...]:
        """Return the longest dependency chain in the graph.

        This is the minimum number of sequential rounds any schedule can
        have, no matter how much parallelism is available. Ties are broken
        by taking the lexicographically smallest chain, so the answer is
        stable.
        """
        order = self.topological_order()
        best: dict[str, tuple[str, ...]] = {}
        for node in order:
            deps = self._deps[node]
            if not deps:
                best[node] = (node,)
                continue
            # Longest first, then alphabetically smallest for a stable pick.
            candidate = min(
                (best[dep] for dep in deps), key=lambda path: (-len(path), path)
            )
            best[node] = candidate + (node,)

        if not best:
            return ()
        return min(best.values(), key=lambda path: (-len(path), path))

    # ------------------------------------------------------------------
    # Cycles and impact
    # ------------------------------------------------------------------
    def find_cycle(self) -> tuple[str, ...] | None:
        """Return one dependency cycle as a closed path, or ``None``.

        The result starts and ends with the same node and each node depends
        on the one after it. Iterative DFS, so a deep graph cannot blow the
        recursion limit.
        """
        white, grey, black = 0, 1, 2
        colour = dict.fromkeys(self._deps, white)
        parent: dict[str, str] = {}

        for start in sorted(self._deps):
            if colour[start] != white:
                continue
            colour[start] = grey
            stack: list[tuple[str, "Iterable[str]"]] = [
                (start, iter(sorted(self._deps[start])))
            ]
            while stack:
                node, children = stack[-1]
                descended = False
                for dep in children:
                    if colour[dep] == grey:
                        # `dep` is still on the stack, so it is an ancestor of
                        # `node`: node -> dep closes a loop. Walk back up the
                        # DFS parents to recover the whole path.
                        path = [node]
                        cursor = node
                        while cursor != dep:
                            cursor = parent[cursor]
                            path.append(cursor)
                        return (node,) + tuple(reversed(path))
                    if colour[dep] == white:
                        parent[dep] = node
                        colour[dep] = grey
                        stack.append((dep, iter(sorted(self._deps[dep]))))
                        descended = True
                        break
                if not descended:
                    colour[node] = black
                    stack.pop()
        return None

    def validate(self) -> None:
        """Raise ``CycleError`` if the graph has a cycle, else do nothing."""
        cycle = self.find_cycle()
        if cycle is not None:
            raise CycleError(cycle)

    def impacted_by(self, node: str) -> tuple[str, ...]:
        """Every node that transitively depends on ``node``, sorted.

        ``node`` itself is never included, even when it sits in a cycle and
        is therefore transitively its own dependent. Useful for "what needs
        re-testing / re-deploying if this changes".
        """
        node = self._require(node)
        seen: set[str] = set()
        queue = [node]
        while queue:
            current = queue.pop()
            for dependent in self._dependents[current]:
                if dependent not in seen:
                    seen.add(dependent)
                    queue.append(dependent)
        seen.discard(node)  # a node in a cycle would otherwise list itself
        return tuple(sorted(seen))

    def subgraph(self, names: Iterable[str]) -> Graph:
        """Return the induced subgraph over ``names``.

        Edges pointing at nodes outside the selection are dropped, so the
        result is always cycle-free if the original was. Handy for slicing a
        build graph down to one package's work.
        """
        keep = {self._require(name) for name in names}
        graph = Graph()
        for name in keep:
            graph.add_node(name)
        for node in keep:
            for dep in self._deps[node] & keep:
                graph.add_dependency(node, dep)
        return graph
