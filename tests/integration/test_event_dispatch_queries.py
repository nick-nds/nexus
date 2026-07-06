"""Regression tests for ``find_dispatchers`` against real FIRES edges.

The existing batch-3 tests only cover the empty / not-found shapes
because the committed demoapp fixture has no FIRES edges. That
gap let a real bug hide: ``event_dispatch`` findings create a
``FIRES`` edge whose target is the ``class:<fqn>`` node, while
``find_dispatchers`` resolved the event to the ``event:<fqn>`` form
(the convention ``LISTENS_TO`` uses) and walked incoming ``FIRES``
on *that* id - so the two never met and every event reported zero
dispatchers.

These tests build a graph with a genuine FIRES edge (via the real
``apply_static_findings`` path) and assert the dispatcher is found,
covering both ways an event node can be stored:

* discovered only by class-naming (``class:<fqn>`` node, no
  ``event:<fqn>`` node) - the DDD ``\\event(new OrderShipped(...))``
  case from the field report;
* registered in the ``$listen`` map (``event:<fqn>`` node).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.builder_findings import apply_static_findings
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import class_id, event_id, listener_id, method_id, route_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.core.reflection.document import StaticAnalysisFinding

pytestmark = pytest.mark.integration

HANDLER = "App\\Modules\\Sales\\Application\\Commands\\ShipOrderCommandHandler"
EVENT = "App\\Modules\\Sales\\Domain\\Events\\OrderShipped"


def _engine_for(graph: Graph, tmp_path: Path) -> QueryEngine:
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="evt")
    assert storage.graph().persist(graph).ok
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    return QueryEngine(registry, ctx)


def _handler_method_node() -> Node:
    return Node(
        id=method_id(HANDLER, "handle"),
        kind=NodeKind.METHOD,
        name="handle",
        attributes={"class_fqn": HANDLER, "line": 75},
    )


def _dispatch_finding() -> StaticAnalysisFinding:
    return StaticAnalysisFinding(
        kind="event_dispatch",
        target=EVENT,
        in_class=HANDLER,
        in_method="handle",
        file="app/Modules/Sales/Application/Commands/ShipOrderCommandHandler.php",
        line=75,
    )


def test_find_dispatchers_resolves_event_stored_as_class_node(tmp_path: Path) -> None:
    # Event discovered by class-naming convention only: a class:<fqn>
    # node with kind=event, and NO event:<fqn> node. This is the modular
    # DDD shape the field report flagged.
    graph = Graph()
    graph.add_node(
        Node(id=class_id(EVENT), kind=NodeKind.EVENT, name="OrderShipped", attributes={}),
    )
    graph.add_node(_handler_method_node())
    apply_static_findings(graph, [_dispatch_finding()])

    engine = _engine_for(graph, tmp_path)
    result = engine.query("find_dispatchers", {"event": EVENT})

    assert result.error is None
    assert result.total == 1
    assert result.dispatchers[0].class_fqn == HANDLER
    assert result.dispatchers[0].method == "handle"
    assert result.dispatchers[0].line == 75


def test_find_dispatchers_resolves_event_stored_as_event_node(tmp_path: Path) -> None:
    # Event registered in the $listen map: an event:<fqn> node exists.
    graph = Graph()
    graph.add_node(
        Node(id=event_id(EVENT), kind=NodeKind.EVENT, name=EVENT, attributes={}),
    )
    graph.add_node(_handler_method_node())
    apply_static_findings(graph, [_dispatch_finding()])

    engine = _engine_for(graph, tmp_path)
    result = engine.query("find_dispatchers", {"event": EVENT})

    assert result.error is None
    assert result.total == 1
    assert result.dispatchers[0].method == "handle"


def test_get_request_flow_event_chain_finds_listeners(tmp_path: Path) -> None:
    # Same root cause as find_dispatchers: the handler FIRES the event
    # (class:<fqn>) while listeners LISTENS_TO the event:<fqn> form. The
    # event chain must bridge both or it reports the event with zero
    # listeners even when listeners exist.
    listener_fqn = "App\\Modules\\Sales\\Application\\Listeners\\NotifyWarehouse"
    rid = route_id("POST", "/orders")

    graph = Graph()
    graph.add_node(
        Node(
            id=rid,
            kind=NodeKind.ROUTE,
            name="/orders",
            attributes={"uri": "/orders", "methods": ["POST"]},
        ),
    )
    graph.add_node(_handler_method_node())
    graph.add_edge(
        Edge(source=rid, target=method_id(HANDLER, "handle"), kind=EdgeKind.ROUTES_TO),
    )
    graph.add_node(
        Node(id=class_id(EVENT), kind=NodeKind.EVENT, name="OrderShipped", attributes={}),
    )
    lid = listener_id(listener_fqn)
    graph.add_node(
        Node(
            id=lid,
            kind=NodeKind.LISTENER,
            name="NotifyWarehouse",
            attributes={"class_fqn": listener_fqn, "method": "handle"},
        ),
    )
    graph.add_edge(Edge(source=lid, target=event_id(EVENT), kind=EdgeKind.LISTENS_TO))
    apply_static_findings(graph, [_dispatch_finding()])

    engine = _engine_for(graph, tmp_path)
    result = engine.query("get_request_flow", {"route_id": rid})

    assert result.error is None
    assert len(result.event_chain) == 1
    assert result.event_chain[0].event == "OrderShipped"
    assert [entry.listener for entry in result.event_chain[0].listeners] == ["NotifyWarehouse"]
