"""Unit tests for :class:`ListByKindTool`."""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.list_by_kind import (
    ListByKindInput,
    ListByKindTool,
)


def _add_class(graph: Graph, fqn: str, kind: NodeKind) -> None:
    graph.add_node(
        Node(
            id=f"class:{fqn}",
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={
                "fqn": fqn,
                "namespace": fqn.rsplit("\\", 1)[0] if "\\" in fqn else None,
                "file": f"/app/{fqn.replace(chr(92), '/')}.php",
            },
        ),
    )


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _make_graph() -> Graph:
    g = Graph()
    _add_class(g, "App\\Events\\OrderPlaced", NodeKind.EVENT)
    _add_class(g, "App\\Events\\PaymentReceived", NodeKind.EVENT)
    _add_class(g, "App\\Modules\\CRM\\Events\\LeadCreated", NodeKind.EVENT)
    _add_class(g, "App\\Jobs\\SendEmail", NodeKind.JOB)
    _add_class(g, "App\\Jobs\\ProcessPayment", NodeKind.JOB)
    _add_class(g, "App\\Models\\User", NodeKind.MODEL)
    return g


def test_list_all_events() -> None:
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(ListByKindInput(kind="event"), ctx)

    assert output.error_code is None
    assert output.kind == "event"
    assert output.total == 3
    assert {r.short_name for r in output.items} == {
        "OrderPlaced",
        "PaymentReceived",
        "LeadCreated",
    }


def test_unknown_kind_returns_invalid_kind_error() -> None:
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(ListByKindInput(kind="nonsense"), ctx)

    assert output.error_code == "invalid_kind"
    # The error message must list valid values so the agent can recover.
    assert "event" in (output.error or "")


def test_route_kind_is_rejected_with_pointer_to_dedicated_tool() -> None:
    """``route`` has its own ``list_routes`` tool — refuse with hint."""
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(ListByKindInput(kind="route"), ctx)

    assert output.error_code == "non_listable_kind"
    assert "list_routes" in (output.error or "")


def test_namespace_prefix_filter() -> None:
    """``namespace_prefix`` scopes results to a module subset."""
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(
        ListByKindInput(kind="event", namespace_prefix="App\\Modules\\CRM"),
        ctx,
    )

    assert output.total == 1
    assert output.items[0].fqn == "App\\Modules\\CRM\\Events\\LeadCreated"


def test_name_glob_filter() -> None:
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(
        ListByKindInput(kind="event", name_glob="*Placed"),
        ctx,
    )

    assert output.total == 1
    assert output.items[0].short_name == "OrderPlaced"


def test_kind_with_zero_matches_returns_empty_not_error() -> None:
    """No notifications in this graph — empty success, not error."""
    ctx = _make_ctx(_make_graph())
    output = ListByKindTool().execute(ListByKindInput(kind="notification"), ctx)

    assert output.error_code is None
    assert output.total == 0
    assert output.items == []


def test_middleware_kind_lists_user_authored_middleware_classes() -> None:
    """Pinning P0-9 from the synthesq-relay audit.

    Before the fix, ``list_by_kind(kind='middleware')`` returned a
    misleading redirect to other tools that didn't actually list
    middleware. Now it enumerates user-authored middleware classes.
    """
    g = _make_graph()
    _add_class(g, "App\\Http\\Middleware\\Authenticate", NodeKind.MIDDLEWARE)
    _add_class(g, "App\\Tenancy\\TenantResolutionMiddleware", NodeKind.MIDDLEWARE)
    ctx = _make_ctx(g)

    output = ListByKindTool().execute(ListByKindInput(kind="middleware"), ctx)

    assert output.error_code is None
    assert output.total == 2
    assert {r.short_name for r in output.items} == {
        "Authenticate",
        "TenantResolutionMiddleware",
    }


def test_middleware_kind_excludes_framework_aliases() -> None:
    """Framework middleware aliases (``middleware:auth``, ``middleware:throttle``)
    use non-``class:`` ids and must not appear in the listing.
    """
    g = _make_graph()
    _add_class(g, "App\\Http\\Middleware\\InjectActingUser", NodeKind.MIDDLEWARE)
    # Framework alias node — id deliberately doesn't start with class:
    g.add_node(
        Node(
            id="middleware:auth",
            kind=NodeKind.MIDDLEWARE,
            name="auth",
            attributes={},
        ),
    )
    ctx = _make_ctx(g)

    output = ListByKindTool().execute(ListByKindInput(kind="middleware"), ctx)

    assert output.total == 1
    assert output.items[0].fqn == "App\\Http\\Middleware\\InjectActingUser"
