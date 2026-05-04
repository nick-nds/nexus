"""Tests for nexus.core.graph types and Graph container."""

from __future__ import annotations

from nexus.core.graph import Edge, EdgeKind, Graph, Node, NodeKind
from nexus.core.outcome import Warning


def make_node(node_id: str, kind: NodeKind = NodeKind.MODEL, name: str = "T") -> Node:
    return Node(id=node_id, kind=kind, name=name)


class TestEnums:
    def test_node_kind_values_are_strings(self) -> None:
        assert NodeKind.MODEL.value == "model"
        assert NodeKind.ROUTE.value == "route"
        # StrEnum members compare equal to their string value.
        assert NodeKind.CONTROLLER == "controller"

    def test_edge_kind_values_are_strings(self) -> None:
        assert EdgeKind.ROUTES_TO.value == "routes_to"
        assert EdgeKind.LISTENS_TO == "listens_to"


class TestNode:
    def test_node_is_hashable(self) -> None:
        n = make_node("class:App\\Models\\User")
        # Hashable lets the builder use Nodes as dict keys / set members.
        assert hash(n) == hash(n)
        assert n in {n}

    def test_attributes_default_to_empty(self) -> None:
        n = make_node("x")
        assert n.attributes == {}

    def test_two_nodes_with_same_id_are_equal(self) -> None:
        a = Node(id="x", kind=NodeKind.MODEL, name="A")
        b = Node(id="x", kind=NodeKind.MODEL, name="A")
        assert a == b


class TestGraphAddNode:
    def test_add_returns_true_on_first_insert(self) -> None:
        g = Graph()
        assert g.add_node(make_node("a"))
        assert len(g) == 1

    def test_add_returns_false_on_duplicate(self) -> None:
        g = Graph()
        g.add_node(make_node("a"))
        assert not g.add_node(make_node("a"))
        assert len(g) == 1

    def test_dedup_works_after_index_built(self) -> None:
        g = Graph()
        g.add_node(make_node("a"))
        g.node_by_id("a")  # build the index
        assert not g.add_node(make_node("a"))
        assert len(g) == 1


class TestGraphAddEdge:
    def test_edges_are_not_deduplicated(self) -> None:
        # Two FIRES edges between the same source/target are legitimate
        # (two call sites). The builder keeps them; the store may collapse.
        g = Graph()
        g.add_edge(Edge(source="a", target="b", kind=EdgeKind.FIRES))
        g.add_edge(Edge(source="a", target="b", kind=EdgeKind.FIRES))
        assert len(g.edges) == 2


class TestGraphLookups:
    def test_node_by_id_returns_none_for_missing(self) -> None:
        g = Graph()
        assert g.node_by_id("nope") is None

    def test_node_by_id_returns_the_node(self) -> None:
        g = Graph()
        g.add_node(make_node("a"))
        n = g.node_by_id("a")
        assert n is not None
        assert n.id == "a"

    def test_edges_from_filters_by_source(self) -> None:
        g = Graph()
        g.add_edge(Edge(source="a", target="b", kind=EdgeKind.CALLS))
        g.add_edge(Edge(source="a", target="c", kind=EdgeKind.CALLS))
        g.add_edge(Edge(source="x", target="b", kind=EdgeKind.CALLS))

        out = list(g.edges_from("a"))
        assert len(out) == 2
        assert all(e.source == "a" for e in out)

    def test_edges_to_filters_by_target(self) -> None:
        g = Graph()
        g.add_edge(Edge(source="a", target="b", kind=EdgeKind.CALLS))
        g.add_edge(Edge(source="x", target="b", kind=EdgeKind.CALLS))
        g.add_edge(Edge(source="a", target="c", kind=EdgeKind.CALLS))

        out = list(g.edges_to("b"))
        assert len(out) == 2


class TestGraphMerge:
    def test_merge_combines_nodes_and_edges(self) -> None:
        a = Graph()
        a.add_node(make_node("x"))
        a.add_edge(Edge(source="x", target="y", kind=EdgeKind.CALLS))

        b = Graph()
        b.add_node(make_node("y"))
        b.add_edge(Edge(source="y", target="z", kind=EdgeKind.CALLS))

        a.merge(b)
        assert len(a) == 2
        assert len(a.edges) == 2

    def test_merge_dedupes_overlapping_nodes(self) -> None:
        a = Graph()
        a.add_node(make_node("x"))
        b = Graph()
        b.add_node(make_node("x"))
        b.add_node(make_node("y"))

        a.merge(b)
        assert len(a) == 2

    def test_merge_carries_warnings(self) -> None:
        a = Graph()
        b = Graph()
        b.add_warning(Warning("dup", "duplicate"))

        a.merge(b)
        assert len(a.warnings) == 1
