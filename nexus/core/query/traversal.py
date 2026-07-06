"""Graph traversal helpers for query tools.

Tools need a small set of common graph walks:

* Direct outgoing/incoming edges of a node filtered by edge kind
* Bounded multi-hop BFS from a starting node
* Node lookup by kind with pagination
* Look up the single handler node a route points to

The implementation reads the in-memory :class:`Graph` the storage
layer hands back via ``storage.graph().load()``. For single-hop
questions that's O(edge count) because we scan; that's fine at
Phase 4's target latencies (the largeapp graph has ~72k edges and
a filtered scan runs in ~5 ms). The recursive CTE pattern from
the design doc becomes relevant once we grow into multi-million
edge graphs; until then the in-memory walker is simpler and
faster.

Why not query SQLite directly
=============================

SQLite's recursive CTEs can do multi-hop walks server-side, and
we'll use them once query latency on big projects bites. For now
the tools load the whole graph on first access and cache it for
the lifetime of the query context - this matches how LanceDB
already materialises vectors into memory-mapped arrays, so the
working set is the same order of magnitude.

All helpers are pure functions of a :class:`Graph`; they never
open the store on their own. Tools typically call
``ctx.storage.graph().load()`` once and pass the returned
:class:`Graph` through to these helpers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind


# Default hard bound on BFS expansion. Prevents a pathological
# graph (e.g. a giant service class connecting to everything) from
# producing unbounded results. Tools that need a different value
# can pass their own ``node_limit`` to ``bfs``.
DEFAULT_NODE_LIMIT = 200

# Default maximum hop depth for BFS. Even the deepest real-world
# request flow tops out at ~6 hops (route → middleware → method →
# action → service → event → listener). 5 is a safe default.
DEFAULT_MAX_DEPTH = 5


def outgoing(graph: Graph, node_id: str, kind: EdgeKind | None = None) -> list[Edge]:
    """Return edges leaving ``node_id``, optionally filtered by kind.

    Uses the graph's lazy outgoing-edge index so repeated calls across
    a multi-thousand-node walk stay O(1) per lookup instead of O(E).
    """
    bucket = graph.outgoing_index().get(node_id, ())
    if kind is None:
        return list(bucket)
    return [e for e in bucket if e.kind == kind]


def incoming(graph: Graph, node_id: str, kind: EdgeKind | None = None) -> list[Edge]:
    """Return edges arriving at ``node_id``, optionally filtered by kind."""
    bucket = graph.incoming_index().get(node_id, ())
    if kind is None:
        return list(bucket)
    return [e for e in bucket if e.kind == kind]


def targets(graph: Graph, node_id: str, kind: EdgeKind) -> list[Node]:
    """Return the target nodes of every edge leaving ``node_id`` with ``kind``.

    Edges whose target isn't in the graph (dangling references,
    e.g. a policy pointing at a vendor model we didn't classify)
    are silently skipped.
    """
    result: list[Node] = []
    for edge in outgoing(graph, node_id, kind):
        target = graph.node_by_id(edge.target)
        if target is not None:
            result.append(target)
    return result


def sources(graph: Graph, node_id: str, kind: EdgeKind) -> list[Node]:
    """Return the source nodes of every edge arriving at ``node_id`` with ``kind``."""
    result: list[Node] = []
    for edge in incoming(graph, node_id, kind):
        source = graph.node_by_id(edge.source)
        if source is not None:
            result.append(source)
    return result


def nodes_of_kind(graph: Graph, kind: NodeKind) -> list[Node]:
    """Return every node of the given kind, in stable id order."""
    result = [n for n in graph.nodes if n.kind == kind]
    result.sort(key=lambda n: n.id)
    return result


def bfs(
    graph: Graph,
    start: str,
    *,
    edge_kinds: Iterable[EdgeKind] | None = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
    node_limit: int = DEFAULT_NODE_LIMIT,
) -> list[tuple[int, Node]]:
    """Breadth-first walk from ``start`` bounded by depth and count.

    Args:
        graph: The graph to walk.
        start: Starting node id. If the id is missing from the
            graph the result is an empty list.
        edge_kinds: Restrict traversal to these edge kinds. ``None``
            means every outgoing edge, regardless of kind.
        max_depth: Maximum hop distance from ``start`` (inclusive).
            ``start`` is at depth 0. Depth 1 means direct neighbours,
            depth 2 means neighbours-of-neighbours, and so on.
        node_limit: Upper bound on total nodes returned. Once the
            limit is hit the walk stops immediately, even mid-hop.

    Returns:
        List of ``(depth, node)`` pairs in visit order. The starting
        node is included at depth 0. Duplicates are filtered - a
        node that would be reached by multiple paths appears once
        at the shallowest depth.
    """
    start_node = graph.node_by_id(start)
    if start_node is None:
        return []

    kinds_set = set(edge_kinds) if edge_kinds is not None else None
    result: list[tuple[int, Node]] = [(0, start_node)]
    visited: set[str] = {start}
    frontier: list[tuple[int, str]] = [(0, start)]

    while frontier and len(result) < node_limit:
        next_frontier: list[tuple[int, str]] = []
        for depth, node_id in frontier:
            if depth >= max_depth:
                continue
            for edge in graph.edges:
                if edge.source != node_id:
                    continue
                if kinds_set is not None and edge.kind not in kinds_set:
                    continue
                if edge.target in visited:
                    continue
                visited.add(edge.target)
                neighbour = graph.node_by_id(edge.target)
                if neighbour is None:
                    continue
                result.append((depth + 1, neighbour))
                if len(result) >= node_limit:
                    return result
                next_frontier.append((depth + 1, edge.target))
        frontier = next_frontier

    return result
