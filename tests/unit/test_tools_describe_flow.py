"""Unit tests for :class:`DescribeFlowTool`.

The tool resolves a fuzzy text query to one or more route nodes and
returns either the full request flow (single confident match) or a
list of candidates (ambiguous). These tests build a small route
graph with controllers, methods, middleware, and an event-fanout
chain, then exercise each match-quality path.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_flow import (
    DescribeFlowInput,
    DescribeFlowTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_route(
    g: Graph,
    *,
    method: str,
    uri: str,
    name: str | None,
    handler_class: str,
    handler_method: str,
) -> str:
    """Add a route + controller method + ROUTES_TO edge. Returns route id."""
    route_id = f"route:{method}:{uri}"
    g.add_node(
        Node(
            id=route_id,
            kind=NodeKind.ROUTE,
            name=uri,
            attributes={
                "uri": uri,
                "methods": [method],
                "name": name,
            },
        ),
    )

    method_id = f"method:{handler_class}::{handler_method}"
    if g.node_by_id(method_id) is None:
        g.add_node(
            Node(
                id=method_id,
                kind=NodeKind.CLASS,
                name=handler_method,
                attributes={
                    "class_fqn": handler_class,
                    "line": 10,
                },
            ),
        )

    class_id = f"class:{handler_class}"
    if g.node_by_id(class_id) is None:
        g.add_node(
            Node(
                id=class_id,
                kind=NodeKind.CONTROLLER,
                name=handler_class.rsplit("\\", 1)[-1],
                attributes={
                    "fqn": handler_class,
                    "file": f"/app/{handler_class.replace(chr(92), '/')}.php",
                },
            ),
        )

    g.add_edge(
        Edge(
            source=route_id,
            target=method_id,
            kind=EdgeKind.ROUTES_TO,
            attributes={},
        ),
    )
    return route_id


def _make_graph() -> Graph:
    """Mini app with three different routes - leads, orders, products."""
    g = Graph()
    _add_route(
        g,
        method="POST",
        uri="/api/leads",
        name="leads.store",
        handler_class="App\\Modules\\CRM\\Http\\Controllers\\LeadController",
        handler_method="store",
    )
    _add_route(
        g,
        method="GET",
        uri="/api/leads",
        name="leads.index",
        handler_class="App\\Modules\\CRM\\Http\\Controllers\\LeadController",
        handler_method="index",
    )
    _add_route(
        g,
        method="POST",
        uri="/api/orders",
        name="orders.store",
        handler_class="App\\Http\\Controllers\\OrderController",
        handler_method="store",
    )
    _add_route(
        g,
        method="GET",
        uri="/api/products/{id}",
        name="products.show",
        handler_class="App\\Http\\Controllers\\ProductController",
        handler_method="show",
    )
    return g


# ---------------------------------------------------------------------------
# Match precedence
# ---------------------------------------------------------------------------


def test_exact_uri_match_returns_single_flow() -> None:
    """``/api/orders`` is an unambiguous exact URI match → flow populated.

    Other routes also share the ``api`` token so they appear in the
    candidate list with ``token_overlap`` quality, but the exact URI
    match is the sole peer at ``exact_uri`` quality, so the tool
    short-circuits to the flow path.
    """
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="/api/orders"),
        ctx,
    )

    assert output.error_code is None
    assert output.flow is not None
    assert output.flow.uri == "/api/orders"
    assert output.flow.handler is not None
    assert output.flow.handler.class_fqn == "App\\Http\\Controllers\\OrderController"
    # Top candidate is the exact match.
    top = output.candidates[0]
    assert top.match_quality == "exact_uri"
    assert top.uri == "/api/orders"


def test_exact_route_name_match_returns_single_flow() -> None:
    """Querying a route name (``leads.store``) resolves to that one route."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="leads.store"),
        ctx,
    )

    assert output.error_code is None
    assert output.flow is not None
    assert output.flow.name == "leads.store"


def test_ambiguous_uri_substring_returns_candidates_only() -> None:
    """``leads`` matches both GET and POST /api/leads → no auto-flow."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="leads"),
        ctx,
    )

    assert output.error_code is None
    assert output.matched >= 2
    # Auto-flow disabled because multiple peers had uri_substring quality.
    assert output.flow is None
    uris = {c.uri for c in output.candidates}
    assert "/api/leads" in uris


def test_token_overlap_matches_verb_noun_phrase() -> None:
    """``"order placement"`` should match the orders route via token overlap."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="order placement"),
        ctx,
    )

    assert output.error_code is None
    assert output.matched >= 1
    top = output.candidates[0]
    assert top.uri == "/api/orders" or "orders" in top.uri.lower()


def test_no_match_returns_structured_error() -> None:
    """An unrelated query returns ``no_matches`` so the agent can recover."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="zznonsense"),
        ctx,
    )

    assert output.error_code == "no_matches"
    assert output.matched == 0
    assert output.candidates == []
    assert output.flow is None


def test_max_candidates_caps_response() -> None:
    """``max_candidates`` trims the candidate list."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="api", max_candidates=2),
        ctx,
    )

    assert output.error_code is None
    assert len(output.candidates) <= 2
    # The matched count reflects the *full* hit count, not the cap.
    assert output.matched >= 2


def test_handler_class_name_matches_route() -> None:
    """``OrderController`` should resolve to the orders route."""
    ctx = _make_ctx(_make_graph())
    output = DescribeFlowTool().execute(
        DescribeFlowInput(query="OrderController"),
        ctx,
    )

    assert output.error_code is None
    assert output.matched >= 1
    top = output.candidates[0]
    assert top.handler_fqn == "App\\Http\\Controllers\\OrderController"
