"""Tests for nexus.core.query.traversal."""

from __future__ import annotations

from nexus.core.graph import Edge, EdgeKind, Graph, Node, NodeKind
from nexus.core.query.traversal import (
    DEFAULT_NODE_LIMIT,
    bfs,
    incoming,
    nodes_of_kind,
    outgoing,
    sources,
    targets,
)


def _node(node_id: str, kind: NodeKind = NodeKind.CLASS) -> Node:
    return Node(id=node_id, kind=kind, name=node_id.rsplit(":", 1)[-1])


def _edge(src: str, tgt: str, kind: EdgeKind = EdgeKind.CALLS) -> Edge:
    return Edge(source=src, target=tgt, kind=kind)


def _graph(nodes: list[Node], edges: list[Edge]) -> Graph:
    g = Graph()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g


# ---------------------------------------------------------------------------
# Edge filters
# ---------------------------------------------------------------------------


class TestOutgoing:
    def test_returns_only_source_match(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("b", "c"), _edge("a", "c")],
        )

        edges = outgoing(g, "a")
        assert len(edges) == 2
        assert all(e.source == "a" for e in edges)

    def test_filters_by_kind(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [
                _edge("a", "b", EdgeKind.CALLS),
                _edge("a", "c", EdgeKind.FIRES),
            ],
        )

        edges = outgoing(g, "a", EdgeKind.FIRES)
        assert len(edges) == 1
        assert edges[0].target == "c"


class TestIncoming:
    def test_filters_by_target_and_kind(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [
                _edge("a", "c", EdgeKind.CALLS),
                _edge("b", "c", EdgeKind.CALLS),
                _edge("a", "b", EdgeKind.CALLS),
            ],
        )

        edges = incoming(g, "c", EdgeKind.CALLS)
        assert len(edges) == 2
        assert {e.source for e in edges} == {"a", "b"}


# ---------------------------------------------------------------------------
# Node resolution
# ---------------------------------------------------------------------------


class TestTargets:
    def test_returns_target_nodes(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("a", "c")],
        )

        nodes = targets(g, "a", EdgeKind.CALLS)
        assert {n.id for n in nodes} == {"b", "c"}

    def test_skips_dangling_targets(self) -> None:
        g = _graph(
            [_node("a")],
            [_edge("a", "missing")],
        )

        assert targets(g, "a", EdgeKind.CALLS) == []


class TestSources:
    def test_returns_source_nodes(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "c"), _edge("b", "c")],
        )

        nodes = sources(g, "c", EdgeKind.CALLS)
        assert {n.id for n in nodes} == {"a", "b"}


class TestNodesOfKind:
    def test_filters_and_sorts(self) -> None:
        g = _graph(
            [
                _node("z", NodeKind.MODEL),
                _node("a", NodeKind.MODEL),
                _node("x", NodeKind.CONTROLLER),
            ],
            [],
        )

        models = nodes_of_kind(g, NodeKind.MODEL)
        assert [n.id for n in models] == ["a", "z"]


# ---------------------------------------------------------------------------
# BFS
# ---------------------------------------------------------------------------


class TestBfs:
    def test_single_hop(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("a", "c")],
        )

        result = bfs(g, "a", max_depth=1)
        depths = {n.id: depth for depth, n in result}
        assert depths == {"a": 0, "b": 1, "c": 1}

    def test_multi_hop(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c"), _node("d")],
            [_edge("a", "b"), _edge("b", "c"), _edge("c", "d")],
        )

        result = bfs(g, "a", max_depth=3)
        depths = {n.id: depth for depth, n in result}
        assert depths["d"] == 3

    def test_max_depth_is_honoured(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [_edge("a", "b"), _edge("b", "c")],
        )

        result = bfs(g, "a", max_depth=1)
        ids = {n.id for _, n in result}
        assert "c" not in ids

    def test_node_limit(self) -> None:
        nodes = [_node(f"n{i}") for i in range(10)]
        edges = [_edge("n0", f"n{i}") for i in range(1, 10)]
        g = _graph(nodes, edges)

        result = bfs(g, "n0", max_depth=1, node_limit=5)
        assert len(result) == 5

    def test_edge_kind_filter(self) -> None:
        g = _graph(
            [_node("a"), _node("b"), _node("c")],
            [
                _edge("a", "b", EdgeKind.CALLS),
                _edge("a", "c", EdgeKind.FIRES),
            ],
        )

        result = bfs(g, "a", edge_kinds=[EdgeKind.FIRES], max_depth=1)
        ids = {n.id for _, n in result}
        assert ids == {"a", "c"}

    def test_cycle_detection(self) -> None:
        g = _graph(
            [_node("a"), _node("b")],
            [_edge("a", "b"), _edge("b", "a")],
        )

        result = bfs(g, "a", max_depth=5)
        ids = [n.id for _, n in result]
        # Each node visited exactly once despite the cycle.
        assert ids == ["a", "b"]

    def test_missing_start_returns_empty(self) -> None:
        g = _graph([_node("a")], [])

        assert bfs(g, "nope") == []

    def test_default_limits_are_conservative(self) -> None:
        # The defaults shouldn't break very small graphs.
        g = _graph([_node("a"), _node("b")], [_edge("a", "b")])
        result = bfs(g, "a")
        assert len(result) == 2
        assert DEFAULT_NODE_LIMIT >= 100
