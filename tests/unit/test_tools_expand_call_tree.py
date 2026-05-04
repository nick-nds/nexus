"""Unit tests for :class:`ExpandCallTreeTool`.

Builds a synthetic call graph: A → B → C → D with a side branch
A → E. Asserts the BFS hits the right nodes at the right depths
in both directions, and that depth/node caps return a structured
truncation reason.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.expand_call_tree import (
    ExpandCallTreeInput,
    ExpandCallTreeTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_method(g: Graph, class_fqn: str, method: str, *, file_: str = "/app.php") -> str:
    method_id = f"method:{class_fqn}::{method}"
    g.add_node(
        Node(
            id=method_id,
            kind=NodeKind.CONTROLLER_METHOD,
            name=method,
            attributes={"class_fqn": class_fqn, "file": file_, "line": 10},
        ),
    )
    return method_id


def _add_call(
    g: Graph,
    src: str,
    dst: str,
    *,
    file_: str = "/caller.php",
    line: int = 42,
) -> None:
    g.add_edge(
        Edge(
            source=src,
            target=dst,
            kind=EdgeKind.CALLS,
            attributes={"file": file_, "line": line},
        ),
    )


def _make_chain_graph() -> tuple[Graph, dict[str, str]]:
    """A → B → C → D, plus A → E and B → F. Returns (graph, name→id)."""
    g = Graph()
    ids = {}
    for klass in ("A", "B", "C", "D", "E", "F"):
        ids[klass] = _add_method(g, f"App\\{klass}", "run")

    _add_call(g, ids["A"], ids["B"], line=1)
    _add_call(g, ids["B"], ids["C"], line=2)
    _add_call(g, ids["C"], ids["D"], line=3)
    _add_call(g, ids["A"], ids["E"], line=4)
    _add_call(g, ids["B"], ids["F"], line=5)
    return g, ids


# ---------------------------------------------------------------------------
# Downstream walk
# ---------------------------------------------------------------------------


def test_downstream_walks_callees_to_max_depth() -> None:
    g, _ids = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\A::run",
            direction="downstream",
            max_depth=3,
        ),
        ctx,
    )

    assert output.error_code is None
    # Direct neighbours: B, E (depth 1). Then C, F (depth 2). Then D (depth 3).
    by_method = {(n.via_class_fqn, n.method, n.depth) for n in output.nodes}
    assert ("App\\A", "run", 1) in by_method  # B reached via A
    assert ("App\\A", "run", 1) in by_method  # E reached via A
    assert ("App\\B", "run", 2) in by_method  # C reached via B
    assert ("App\\B", "run", 2) in by_method  # F reached via B
    assert ("App\\C", "run", 3) in by_method  # D reached via C


def test_downstream_max_depth_one_only_returns_direct_neighbours() -> None:
    g, _ids = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\A::run",
            direction="downstream",
            max_depth=1,
        ),
        ctx,
    )

    depths = {n.depth for n in output.nodes}
    assert depths == {1}
    methods = {(n.via_class_fqn, n.via_method, n.method) for n in output.nodes}
    assert ("App\\A", "run", "run") in methods  # B and E both have name 'run'
    assert output.truncated is True
    assert output.truncated_reason == "max_depth"


# ---------------------------------------------------------------------------
# Upstream walk
# ---------------------------------------------------------------------------


def test_upstream_walks_callers_recursively() -> None:
    g, _ids = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\D::run",
            direction="upstream",
            max_depth=5,
        ),
        ctx,
    )

    # D ← C ← B ← A. Three callers.
    classes = {n.via_class_fqn for n in output.nodes if n.via_class_fqn is not None}
    assert {"App\\C", "App\\B", "App\\A"}.issubset({n.class_fqn for n in output.nodes})
    # ``via`` for each upstream step is the callee we walked from.
    assert "App\\D" in classes  # C was reached via D
    assert "App\\C" in classes  # B was reached via C
    assert "App\\B" in classes  # A was reached via B


# ---------------------------------------------------------------------------
# Caps and edge cases
# ---------------------------------------------------------------------------


def test_unknown_method_returns_structured_error() -> None:
    g, _ = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(method_fqn="App\\Nope::run"),
        ctx,
    )

    assert output.error_code == "method_not_found"
    assert output.nodes == []


def test_max_nodes_caps_response_with_reason() -> None:
    g, _ = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\A::run",
            direction="downstream",
            max_depth=10,
            max_nodes=2,
        ),
        ctx,
    )

    assert len(output.nodes) == 2
    assert output.truncated is True
    assert output.truncated_reason == "max_nodes"


def test_full_walk_no_truncation() -> None:
    """Fully-traversed tree returns ``truncated=False``."""
    g, _ = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\A::run",
            direction="downstream",
            max_depth=10,
            max_nodes=100,
        ),
        ctx,
    )

    # B, E, C, F, D = 5 reachable downstream from A.
    assert output.total == 5
    assert output.truncated is False
    assert output.truncated_reason is None


def test_call_site_propagated_from_edge_attributes() -> None:
    """The edge's ``file`` / ``line`` should land on ``call_site_*``."""
    g, _ids = _make_chain_graph()
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(
            method_fqn="App\\A::run",
            direction="downstream",
            max_depth=1,
        ),
        ctx,
    )

    # The A→B edge was added with line=1.
    b_node = next(n for n in output.nodes if n.class_fqn == "App\\B")
    assert b_node.call_site_line == 1
    assert b_node.call_site_file == "/caller.php"


def test_no_calls_edges_returns_empty_success() -> None:
    """When LSP didn't run, the tool returns an empty list — not an error."""
    g = Graph()
    method_id = _add_method(g, "App\\Solo", "run")
    assert method_id  # method exists; no edges
    ctx = _make_ctx(g)

    output = ExpandCallTreeTool().execute(
        ExpandCallTreeInput(method_fqn="App\\Solo::run"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 0
    assert output.nodes == []
    assert output.truncated is False
