"""Unit tests for :class:`DescribeModuleTool`."""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_module import (
    DescribeModuleInput,
    DescribeModuleTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_class(g: Graph, fqn: str, kind: NodeKind) -> None:
    g.add_node(
        Node(
            id=f"class:{fqn}",
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn},
        ),
    )


def _add_route(
    g: Graph,
    *,
    method: str,
    uri: str,
    name: str,
    handler_class: str,
    handler_method: str,
) -> None:
    route_id = f"route:{method}:{uri}"
    g.add_node(
        Node(
            id=route_id,
            kind=NodeKind.ROUTE,
            name=uri,
            attributes={"uri": uri, "methods": [method], "name": name},
        ),
    )
    method_id = f"method:{handler_class}::{handler_method}"
    if g.node_by_id(method_id) is None:
        g.add_node(
            Node(
                id=method_id,
                kind=NodeKind.METHOD,
                name=handler_method,
                attributes={"class_fqn": handler_class},
            ),
        )
    g.add_edge(
        Edge(source=route_id, target=method_id, kind=EdgeKind.ROUTES_TO, attributes={}),
    )


def _make_graph() -> Graph:
    g = Graph()
    # CRM module
    _add_class(g, "App\\Modules\\CRM\\Customers\\Customer", NodeKind.MODEL)
    _add_class(g, "App\\Modules\\CRM\\Customers\\CustomerController", NodeKind.CONTROLLER)
    _add_class(g, "App\\Modules\\CRM\\Leads\\Lead", NodeKind.MODEL)
    _add_class(g, "App\\Modules\\CRM\\Events\\LeadCreated", NodeKind.EVENT)
    # Operations module (different prefix - should be excluded)
    _add_class(g, "App\\Modules\\Operations\\Products\\Product", NodeKind.MODEL)

    _add_route(
        g,
        method="GET",
        uri="/api/customers",
        name="customers.index",
        handler_class="App\\Modules\\CRM\\Customers\\CustomerController",
        handler_method="index",
    )
    _add_route(
        g,
        method="GET",
        uri="/api/products",
        name="products.index",
        handler_class="App\\Modules\\Operations\\Products\\ProductController",
        handler_method="index",
    )
    return g


def test_describe_module_returns_class_breakdown_by_kind() -> None:
    ctx = _make_ctx(_make_graph())
    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\CRM"),
        ctx,
    )

    assert output.error_code is None
    assert output.total_classes == 4
    by_kind = {row.kind: row for row in output.classes_by_kind}
    assert by_kind["model"].total == 2
    assert by_kind["controller"].total == 1
    assert by_kind["event"].total == 1


def test_describe_module_lists_submodules() -> None:
    ctx = _make_ctx(_make_graph())
    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\CRM"),
        ctx,
    )

    assert set(output.submodules) == {
        "App\\Modules\\CRM\\Customers",
        "App\\Modules\\CRM\\Leads",
        "App\\Modules\\CRM\\Events",
    }


def test_describe_module_only_includes_routes_with_handler_in_module() -> None:
    """Routes whose handler lives outside ``prefix`` are excluded."""
    ctx = _make_ctx(_make_graph())
    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\CRM"),
        ctx,
    )

    uris = {r.uri for r in output.routes}
    assert "/api/customers" in uris
    assert "/api/products" not in uris  # belongs to Operations


def test_describe_module_unknown_prefix_returns_structured_error() -> None:
    ctx = _make_ctx(_make_graph())
    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\Nonsense"),
        ctx,
    )

    assert output.error_code == "empty_module"
    assert output.total_classes == 0
    assert output.classes_by_kind == []


def test_describe_module_strips_trailing_backslash_in_prefix() -> None:
    """``App\\Modules\\CRM\\`` and ``App\\Modules\\CRM`` resolve identically."""
    ctx = _make_ctx(_make_graph())
    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\CRM\\"),
        ctx,
    )

    assert output.error_code is None
    assert output.total_classes == 4


def test_sample_per_kind_caps_fqns_but_reports_full_total() -> None:
    g = Graph()
    for i in range(20):
        _add_class(g, f"App\\Modules\\Big\\Models\\Item{i:02d}", NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\Big", sample_per_kind=3),
        ctx,
    )

    assert output.error_code is None
    [models] = output.classes_by_kind
    assert models.kind == "model"
    assert models.total == 20
    assert len(models.fqns) == 3


def test_fqns_truncation_is_signalled_explicitly() -> None:
    """Pinning P1-14 from the acme-platform audit.

    Before the fix, ``describe_module`` silently capped per-kind
    ``fqns`` at ``sample_per_kind`` while leaving ``truncated=false``
    and ``truncated_lists=[]``. Agents had no signal that the FQN
    list was a sample, only the total/len comparison.
    """
    g = Graph()
    for i in range(20):
        _add_class(g, f"App\\Modules\\Big\\Models\\Item{i:02d}", NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\Big", sample_per_kind=3),
        ctx,
    )

    assert output.truncated is True
    # Format mirrors list_by_kind: "<path>:<full>→<cut>".
    assert any("classes_by_kind[model].fqns:20→3" in path for path in output.truncated_lists)


def test_no_truncation_signal_when_sample_size_covers_all() -> None:
    """Sample size large enough → no truncation flag."""
    g = Graph()
    for i in range(3):
        _add_class(g, f"App\\Modules\\Tiny\\Models\\Item{i:02d}", NodeKind.MODEL)
    ctx = _make_ctx(g)

    output = DescribeModuleTool().execute(
        DescribeModuleInput(prefix="App\\Modules\\Tiny", sample_per_kind=10),
        ctx,
    )

    assert output.truncated is False
    assert output.truncated_lists == []
    [models] = output.classes_by_kind
    assert len(models.fqns) == 3
    assert models.total == 3
