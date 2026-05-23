"""Integration-style tests for the case-insensitive FQN fallback (audit P1-17).

The :func:`resolve_class_id` helper is exercised in
``test_tools_resolve_class_id.py``; this file verifies that the four
FQN-input tools (describe_class, get_model_context, get_policy_for,
find_implementations) actually pass the case-correction warning
through to their response envelopes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_class import DescribeClassInput, DescribeClassTool
from nexus.core.query.tools.find_implementations import (
    FindImplementationsInput,
    FindImplementationsTool,
)
from nexus.core.query.tools.get_model_context import (
    GetModelContextInput,
    GetModelContextTool,
)
from nexus.core.query.tools.get_policy_for import GetPolicyForInput, GetPolicyForTool


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_class(graph: Graph, fqn: str, *, kind: NodeKind = NodeKind.CLASS) -> str:
    node_id = f"class:{fqn}"
    graph.add_node(
        Node(
            id=node_id,
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn, "namespace": "\\".join(fqn.split("\\")[:-1])},
        ),
    )
    return node_id


def test_describe_class_surfaces_case_correction_warning() -> None:
    g = Graph()
    _add_class(g, "App\\Models\\User", kind=NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="app\\models\\user"),
        ctx,
    )

    assert output.error_code is None
    assert len(output.warnings) == 1
    assert "case-corrected" in output.warnings[0]


def test_describe_class_no_warning_on_exact_match() -> None:
    g = Graph()
    _add_class(g, "App\\Models\\User", kind=NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Models\\User"),
        ctx,
    )

    assert output.warnings == []


def test_describe_class_no_warning_when_class_not_found() -> None:
    """Genuinely-missing FQN returns class_not_found, no case-correction."""
    g = Graph()
    _add_class(g, "App\\Models\\User", kind=NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Models\\Nonexistent"),
        ctx,
    )

    assert output.error_code == "class_not_found"
    assert output.warnings == []


def test_get_model_context_surfaces_case_correction_warning() -> None:
    g = Graph()
    _add_class(g, "App\\Models\\Order", kind=NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = GetModelContextTool().execute(
        GetModelContextInput(fqn="APP\\MODELS\\ORDER"),
        ctx,
    )

    assert output.error_code is None
    assert len(output.warnings) == 1
    assert "case-corrected" in output.warnings[0]


def test_find_implementations_surfaces_case_correction_warning() -> None:
    g = Graph()
    _add_class(g, "App\\Contracts\\Repository", kind=NodeKind.CLASS)
    impl_id = _add_class(g, "App\\Repos\\UserRepository", kind=NodeKind.CLASS)
    g.add_edge(
        Edge(
            source=impl_id,
            target="class:App\\Contracts\\Repository",
            kind=EdgeKind.IMPLEMENTS,
            attributes={},
        ),
    )
    ctx = _make_ctx(g)

    output = FindImplementationsTool().execute(
        FindImplementationsInput(interface_fqn="app\\contracts\\repository"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 1
    assert len(output.warnings) == 1
    assert "case-corrected" in output.warnings[0]


def test_get_policy_for_surfaces_case_correction_warning() -> None:
    g = Graph()
    model_id = _add_class(g, "App\\Models\\Order", kind=NodeKind.MODEL)
    policy_id = _add_class(g, "App\\Policies\\OrderPolicy", kind=NodeKind.POLICY)
    g.add_edge(
        Edge(
            source=policy_id,
            target=model_id,
            kind=EdgeKind.APPLIES_TO,
            attributes={},
        ),
    )
    ctx = _make_ctx(g)

    output = GetPolicyForTool().execute(
        GetPolicyForInput(model_fqn="app\\models\\order"),
        ctx,
    )

    assert output.error_code is None
    assert output.policy_fqn == "App\\Policies\\OrderPolicy"
    assert len(output.warnings) == 1
    assert "case-corrected" in output.warnings[0]
