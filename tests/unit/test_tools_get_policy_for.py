"""Unit tests for the ``get_policy_for`` query tool.

Exercises all branches of ``GetPolicyForTool.execute`` using a
hand-built in-memory graph and a lightweight storage stub.
"""

from __future__ import annotations

from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import class_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.get_policy_for import (
    GetPolicyForInput,
    GetPolicyForTool,
)

# ---------------------------------------------------------------------------
# Stub storage
# ---------------------------------------------------------------------------


class _StubGraphStore:
    def __init__(self, graph: Graph) -> None:
        self._graph = graph

    def load(self) -> Graph:
        return self._graph


class _StubStorage:
    def __init__(self, graph: Graph) -> None:
        self._graph_store = _StubGraphStore(graph)

    def graph(self) -> _StubGraphStore:
        return self._graph_store


def _make_ctx(graph: Graph) -> QueryContext:
    return QueryContext(
        storage=_StubStorage(graph),  # type: ignore[arg-type]
        budget=ResponseBudget(),
    )


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------


_MODEL_FQN = "App\\Models\\Order"
_POLICY_FQN = "App\\Policies\\OrderPolicy"


def _build_graph_with_policy() -> Graph:
    """Build a graph with a model, a policy, and ability methods."""
    g = Graph()

    # Model node
    g.add_node(
        Node(
            id=class_id(_MODEL_FQN),
            kind=NodeKind.MODEL,
            name="Order",
            attributes={"fqn": _MODEL_FQN},
        )
    )

    # Policy node
    g.add_node(
        Node(
            id=class_id(_POLICY_FQN),
            kind=NodeKind.POLICY,
            name="OrderPolicy",
            attributes={
                "fqn": _POLICY_FQN,
                "file": "app/Policies/OrderPolicy.php",
            },
        )
    )

    # Policy → model APPLIES_TO edge
    g.add_edge(
        Edge(
            source=class_id(_POLICY_FQN),
            target=class_id(_MODEL_FQN),
            kind=EdgeKind.APPLIES_TO,
        )
    )

    # Method nodes on the policy
    for method_name, line in [("view", 20), ("update", 30), ("delete", 40)]:
        method_id = f"method:{_POLICY_FQN}::{method_name}"
        g.add_node(
            Node(
                id=method_id,
                kind=NodeKind.CONTROLLER_METHOD,
                name=method_name,
                attributes={"visibility": "public", "line": line},
            )
        )
        g.add_edge(
            Edge(
                source=method_id,
                target=class_id(_POLICY_FQN),
                kind=EdgeKind.PART_OF,
            )
        )

    return g


# ---------------------------------------------------------------------------
# GetPolicyForTool.execute
# ---------------------------------------------------------------------------


class TestGetPolicyForExecute:
    def setup_method(self) -> None:
        self.tool = GetPolicyForTool()

    def test_returns_policy_for_known_model(self) -> None:
        g = _build_graph_with_policy()
        ctx = _make_ctx(g)
        payload = GetPolicyForInput(model_fqn=_MODEL_FQN)

        result = self.tool.execute(payload, ctx)

        assert result.error is None
        assert result.policy_fqn == _POLICY_FQN
        assert result.policy_short_name == "OrderPolicy"
        assert result.file == "app/Policies/OrderPolicy.php"

    def test_methods_are_sorted(self) -> None:
        g = _build_graph_with_policy()
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        names = [m.name for m in result.methods]
        assert names == sorted(names)

    def test_method_attributes_populated(self) -> None:
        g = _build_graph_with_policy()
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        by_name = {m.name: m for m in result.methods}
        assert by_name["view"].visibility == "public"
        assert by_name["view"].line == 20

    def test_methods_carry_node_id_passable_to_get_node_body(self) -> None:
        g = _build_graph_with_policy()
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        for method in result.methods:
            assert method.node_id.startswith(f"method:{_POLICY_FQN}::")
            assert method.node_id.endswith(f"::{method.name}")

    def test_model_not_found_returns_error(self) -> None:
        g = Graph()
        ctx = _make_ctx(g)
        payload = GetPolicyForInput(model_fqn="App\\Models\\Ghost")

        result = self.tool.execute(payload, ctx)

        assert result.error is not None
        assert result.error_code == "model_not_found"

    def test_model_without_policy_returns_error(self) -> None:
        g = Graph()
        g.add_node(
            Node(
                id=class_id(_MODEL_FQN),
                kind=NodeKind.MODEL,
                name="Order",
                attributes={"fqn": _MODEL_FQN},
            )
        )
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        assert result.error is not None
        assert result.error_code == "policy_not_found"

    def test_dangling_method_source_is_skipped(self) -> None:
        """An PART_OF edge pointing to a missing source is silently skipped."""
        g = _build_graph_with_policy()
        # Add a dangling PART_OF edge
        g.add_edge(
            Edge(
                source="method:Missing::phantom",
                target=class_id(_POLICY_FQN),
                kind=EdgeKind.PART_OF,
            )
        )
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        # Should succeed and return the 3 real methods, skipping the dangling edge
        assert result.error is None
        assert len(result.methods) == 3

    def test_policy_node_name_used_as_fqn_fallback(self) -> None:
        """When fqn attribute is absent, node.name is used as policy_fqn."""
        g = Graph()
        g.add_node(
            Node(
                id=class_id(_MODEL_FQN),
                kind=NodeKind.MODEL,
                name="Order",
            )
        )
        g.add_node(
            Node(
                id=class_id(_POLICY_FQN),
                kind=NodeKind.POLICY,
                name="OrderPolicy",
                # No fqn attribute
            )
        )
        g.add_edge(
            Edge(
                source=class_id(_POLICY_FQN),
                target=class_id(_MODEL_FQN),
                kind=EdgeKind.APPLIES_TO,
            )
        )
        ctx = _make_ctx(g)
        result = self.tool.execute(GetPolicyForInput(model_fqn=_MODEL_FQN), ctx)

        assert result.error is None
        # Falls back to node.name when fqn attr is missing
        assert result.policy_fqn == "OrderPolicy"
