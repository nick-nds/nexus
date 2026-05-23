"""Pinning audit P0-7: find_implementations now defaults to inclusive.

Before this change, ``include_subclasses`` defaulted to ``false`` so
abstract-class targets returned zero results. That made the tool
useless for the dominant Laravel-codebase pattern: abstract base
classes like ``Module``, ``SynthesQEvent``, ``BaseListener``.

The default flipped to ``true`` so a caller can ask
``find_implementations(interface_fqn="App\\Modules\\Module")`` and
get all 20+ subclasses without an opt-in.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.find_implementations import (
    FindImplementationsInput,
    FindImplementationsTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_class(g: Graph, fqn: str, kind: NodeKind = NodeKind.CLASS) -> str:
    cid = f"class:{fqn}"
    g.add_node(
        Node(
            id=cid,
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn},
        ),
    )
    return cid


def _extends(g: Graph, child_id: str, parent_id: str) -> None:
    g.add_edge(Edge(source=child_id, target=parent_id, kind=EdgeKind.EXTENDS, attributes={}))


def _implements(g: Graph, class_id: str, interface_id: str) -> None:
    g.add_edge(
        Edge(source=class_id, target=interface_id, kind=EdgeKind.IMPLEMENTS, attributes={}),
    )


def test_abstract_class_target_returns_subclasses_by_default() -> None:
    """The fix for P0-7: abstract-class target → subclasses without opt-in."""
    g = Graph()
    module_id = _add_class(g, "App\\Modules\\Module")
    customers_id = _add_class(g, "App\\Modules\\CustomersModule")
    leads_id = _add_class(g, "App\\Modules\\LeadsModule")
    _extends(g, customers_id, module_id)
    _extends(g, leads_id, module_id)
    ctx = _make_ctx(g)

    # No ``include_subclasses=True`` — relies on the new default.
    output = FindImplementationsTool().execute(
        FindImplementationsInput(interface_fqn="App\\Modules\\Module"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 2
    fqns = {r.fqn for r in output.implementations}
    assert fqns == {"App\\Modules\\CustomersModule", "App\\Modules\\LeadsModule"}
    assert all(r.via == "extends" for r in output.implementations)


def test_interface_target_returns_implementers() -> None:
    """Interfaces still work the same way they always have."""
    g = Graph()
    interface_id = _add_class(g, "App\\Contracts\\Transformer", kind=NodeKind.INTERFACE)
    impl_a = _add_class(g, "App\\Repos\\AddressTransformer")
    impl_b = _add_class(g, "App\\Repos\\NameTransformer")
    _implements(g, impl_a, interface_id)
    _implements(g, impl_b, interface_id)
    ctx = _make_ctx(g)

    output = FindImplementationsTool().execute(
        FindImplementationsInput(interface_fqn="App\\Contracts\\Transformer"),
        ctx,
    )

    assert output.total == 2
    assert all(r.via == "implements" for r in output.implementations)


def test_include_subclasses_false_restricts_to_implements_only() -> None:
    """Legacy opt-out behaviour for callers who specifically want the
    interface-only set."""
    g = Graph()
    base_id = _add_class(g, "App\\Modules\\Module")
    sub_id = _add_class(g, "App\\Modules\\CustomersModule")
    iface_id = _add_class(g, "App\\Contracts\\Transformer", kind=NodeKind.INTERFACE)
    impl_id = _add_class(g, "App\\Repos\\AddressTransformer")
    _extends(g, sub_id, base_id)
    _implements(g, impl_id, iface_id)
    ctx = _make_ctx(g)

    # Module target with include_subclasses=False → zero (no IMPLEMENTS).
    output = FindImplementationsTool().execute(
        FindImplementationsInput(
            interface_fqn="App\\Modules\\Module",
            include_subclasses=False,
        ),
        ctx,
    )
    assert output.total == 0

    # Interface target with include_subclasses=False → unchanged.
    output = FindImplementationsTool().execute(
        FindImplementationsInput(
            interface_fqn="App\\Contracts\\Transformer",
            include_subclasses=False,
        ),
        ctx,
    )
    assert output.total == 1
