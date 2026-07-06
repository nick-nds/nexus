"""Tests for the ``bus_dispatch`` finding handler (CQRS-bus blindness fix).

A CQRS command/query bus resolves its handler by naming-convention
reflection at runtime, so an LSP sees no static reference from a
dispatch site (``$queryBus->ask(new EvaluateRoutingContextQuery())``)
to the handler's ``handle`` method. ``find_callers`` therefore misses
every real dispatch site.

The Phase C ``BusDispatchVisitor`` emits a ``bus_dispatch`` finding at
each dispatch site carrying the message class. The graph builder
resolves the message to its handler *by short name* (the handler almost
always lives in a different sub-namespace than its message) and adds a
synthetic ``CALLS`` edge from the dispatch site to the handler's
``handle`` method, tagged ``via: bus_convention`` so it stays
distinguishable from LSP-verified call edges.
"""

from __future__ import annotations

from nexus.core.graph.builder_findings import apply_static_findings
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import class_id, method_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.reflection.document import StaticAnalysisFinding


def _class(fqn: str) -> Node:
    return Node(id=class_id(fqn), kind=NodeKind.CLASS, name=fqn, attributes={})


def _method(class_fqn: str, name: str) -> Node:
    return Node(
        id=method_id(class_fqn, name),
        kind=NodeKind.METHOD,
        name=name,
        attributes={"class_fqn": class_fqn},
    )


def _dispatch_finding(
    *,
    message: str,
    in_class: str,
    in_method: str,
    file: str | None = "/app/Modules/Routing/Presentation/RoutingController.php",
    line: int | None = 55,
) -> StaticAnalysisFinding:
    return StaticAnalysisFinding(
        kind="bus_dispatch",
        target=message,
        in_class=in_class,
        in_method=in_method,
        file=file,
        line=line,
        meta={"method": "ask"},
    )


def _calls_edges(graph: Graph) -> list[Edge]:
    return [e for e in graph.edges if e.kind == EdgeKind.CALLS]


def test_dispatch_site_links_to_handler_handle_method_across_namespaces() -> None:
    """The reporter's case: message and handler live in *different*
    sub-namespaces (``...\\Queries`` vs ``...\\QueryHandlers``), so the
    handler must be resolved by short name, not by FQN + suffix."""
    base = "App\\Modules\\Routing\\Application"
    message = f"{base}\\Queries\\EvaluateRoutingContextQuery"
    handler = f"{base}\\QueryHandlers\\EvaluateRoutingContextQueryHandler"
    dispatcher = "App\\Modules\\Routing\\Presentation\\RoutingController"
    graph = Graph()
    graph.add_node(_class(message))
    graph.add_node(_class(handler))
    graph.add_node(_method(handler, "handle"))
    graph.add_node(_class(dispatcher))
    graph.add_node(_method(dispatcher, "resolve"))

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="resolve")],
    )

    calls = _calls_edges(graph)
    assert len(calls) == 1
    edge = calls[0]
    assert edge.source == method_id(dispatcher, "resolve")
    assert edge.target == method_id(handler, "handle")
    assert edge.attributes["via"] == "bus_convention"
    assert edge.attributes["synthetic"] is True
    assert edge.attributes["message"] == message
    assert edge.attributes["file"] == "/app/Modules/Routing/Presentation/RoutingController.php"
    assert edge.attributes["line"] == 55


def test_no_edge_when_handler_class_is_absent() -> None:
    """A ``new SomeJob()`` dispatched through a bus-shaped call has no
    ``SomeJobHandler`` class, so nothing is linked - the graph-existence
    check makes the PHP heuristic self-correcting."""
    message = "App\\Jobs\\SendWelcomeEmail"
    dispatcher = "App\\Http\\Controllers\\SignupController"
    graph = Graph()
    graph.add_node(_class(message))
    graph.add_node(_class(dispatcher))
    graph.add_node(_method(dispatcher, "store"))

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="store")],
    )

    assert _calls_edges(graph) == []


def test_ambiguous_handler_short_name_is_skipped() -> None:
    """Two handlers with the same short name in different modules is
    ambiguous; linking to both would be wrong, so we link to neither."""
    message = "App\\Modules\\A\\Commands\\ArchiveCommand"
    handler_a = "App\\Modules\\A\\Handlers\\ArchiveCommandHandler"
    handler_b = "App\\Modules\\B\\Handlers\\ArchiveCommandHandler"
    dispatcher = "App\\Http\\Controllers\\AdminController"
    graph = Graph()
    for fqn in (message, handler_a, handler_b, dispatcher):
        graph.add_node(_class(fqn))
    graph.add_node(_method(handler_a, "handle"))
    graph.add_node(_method(handler_b, "handle"))
    graph.add_node(_method(dispatcher, "archive"))

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="archive")],
    )

    assert _calls_edges(graph) == []
    # The ambiguity is surfaced, not dropped silently.
    warnings = [w for w in graph.warnings if w.code == "bus_handler_ambiguous"]
    assert len(warnings) == 1
    assert warnings[0].context["message"] == message
    assert warnings[0].context["candidates"] == sorted([handler_a, handler_b])


def test_command_suffix_is_stripped_for_handler_resolution() -> None:
    """Codebases that name the handler ``CreateUserHandler`` for message
    ``CreateUserCommand`` (dropping the suffix) are also resolved."""
    message = "App\\Modules\\Users\\Commands\\CreateUserCommand"
    handler = "App\\Modules\\Users\\Handlers\\CreateUserHandler"
    dispatcher = "App\\Http\\Controllers\\UserController"
    graph = Graph()
    for fqn in (message, handler, dispatcher):
        graph.add_node(_class(fqn))
    graph.add_node(_method(handler, "handle"))
    graph.add_node(_method(dispatcher, "store"))

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="store")],
    )

    calls = _calls_edges(graph)
    assert len(calls) == 1
    assert calls[0].target == method_id(handler, "handle")


def test_falls_back_to_invoke_when_handler_has_no_handle_method() -> None:
    """Single-action handlers expose ``__invoke`` instead of ``handle``."""
    message = "App\\Modules\\Billing\\Commands\\ChargeCardCommand"
    handler = "App\\Modules\\Billing\\Handlers\\ChargeCardCommandHandler"
    dispatcher = "App\\Http\\Controllers\\CheckoutController"
    graph = Graph()
    for fqn in (message, handler, dispatcher):
        graph.add_node(_class(fqn))
    graph.add_node(_method(handler, "__invoke"))
    graph.add_node(_method(dispatcher, "pay"))

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="pay")],
    )

    calls = _calls_edges(graph)
    assert len(calls) == 1
    assert calls[0].target == method_id(handler, "__invoke")


def test_no_edge_when_handler_class_has_no_dispatch_method() -> None:
    """The handler *class* exists but exposes neither ``handle`` nor
    ``__invoke`` (e.g. an abstract base picked up by short name); with no
    method to point the edge at, we add nothing rather than dangle."""
    message = "App\\Modules\\Ops\\Commands\\RebootCommand"
    handler = "App\\Modules\\Ops\\Handlers\\RebootCommandHandler"
    dispatcher = "App\\Http\\Controllers\\OpsController"
    graph = Graph()
    for fqn in (message, handler, dispatcher):
        graph.add_node(_class(fqn))
    graph.add_node(_method(dispatcher, "reboot"))  # handler has no methods

    apply_static_findings(
        graph,
        [_dispatch_finding(message=message, in_class=dispatcher, in_method="reboot")],
    )

    assert _calls_edges(graph) == []


def test_edge_is_created_when_dispatch_site_has_no_file_or_line() -> None:
    """File/line are optional on the finding; the edge is still linked,
    just without those call-site attributes."""
    message = "App\\Modules\\Users\\Queries\\FindUserQuery"
    handler = "App\\Modules\\Users\\QueryHandlers\\FindUserQueryHandler"
    dispatcher = "App\\Http\\Controllers\\UserController"
    graph = Graph()
    for fqn in (message, handler, dispatcher):
        graph.add_node(_class(fqn))
    graph.add_node(_method(handler, "handle"))
    graph.add_node(_method(dispatcher, "show"))

    apply_static_findings(
        graph,
        [
            _dispatch_finding(
                message=message,
                in_class=dispatcher,
                in_method="show",
                file=None,
                line=None,
            )
        ],
    )

    calls = _calls_edges(graph)
    assert len(calls) == 1
    assert "file" not in calls[0].attributes
    assert "line" not in calls[0].attributes
    assert calls[0].target == method_id(handler, "handle")


def test_finding_without_message_or_context_is_skipped() -> None:
    """Defensive: a bus_dispatch finding missing its target message or
    its enclosing method resolves to nothing rather than crashing."""
    graph = Graph()
    graph.add_node(_class("App\\Some\\XHandler"))
    graph.add_node(_method("App\\Some\\XHandler", "handle"))

    apply_static_findings(
        graph,
        [
            StaticAnalysisFinding(
                kind="bus_dispatch",
                target=None,
                in_class="App\\Http\\Controllers\\C",
                in_method="index",
            ),
            StaticAnalysisFinding(
                kind="bus_dispatch",
                target="App\\Some\\X",
                in_class="App\\Http\\Controllers\\C",
                in_method=None,
            ),
        ],
    )

    assert _calls_edges(graph) == []
