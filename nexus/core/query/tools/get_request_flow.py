"""``get_request_flow`` — multi-hop view of a route's full fan-out.

Extends :mod:`trace_route` with one more level of causality:
for every event the handler fires, also list the listeners that
subscribe to it, and for each listener surface any *further*
events/jobs that listener fires.

This is the tool that answers questions like *"what actually
happens when a user hits ``POST /orders``?"* — the agent sees
the immediate middleware + controller, the side-effect events,
and the downstream listener work in one shot, without having to
issue a cascade of follow-up queries.

Depth is capped: the walk only goes **route → handler → event →
listener → {events,jobs}**. Going further would grow
combinatorially on dense graphs and is better served by a
targeted ``find_event_chains`` call (batch 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr, str_list_attr
from nexus.core.query.tools.trace_route import (
    RouteTraceHandler,
    TraceRouteInput,
    _collect_middleware,
    _collect_policies,
    _collect_targets,
    _resolve_error_message,
    _resolve_form_request,
    _resolve_handler,
    _resolve_route,
)
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


class GetRequestFlowInput(ToolInput):
    """Identify the route to walk.

    Same shape as :class:`trace_route`'s input — either pass a
    ``route_id`` or a ``(method, uri)`` pair.
    """

    route_id: str | None = Field(
        default=None,
        description="Stable id of the route, e.g. ``route:GET:/api/users``.",
    )
    method: str | None = Field(
        default=None,
        description="HTTP verb to match when resolving by URI. Case-insensitive.",
    )
    uri: str | None = Field(
        default=None,
        description="Exact URI to match. Required if ``route_id`` is not supplied.",
    )


class ListenerFanOut(ToolOutput):
    """One listener reached from a fired event.

    The ``fires`` / ``dispatches`` / ``notifies`` lists are the
    *downstream* work the listener itself does — the "and then what"
    level.
    """

    listener: str
    listener_fqn: str | None = None
    fires: list[str] = Field(default_factory=list)
    dispatches: list[str] = Field(default_factory=list)
    notifies: list[str] = Field(default_factory=list)


class EventFanOut(ToolOutput):
    """An event fired by the handler plus its listeners."""

    event: str
    listeners: list[ListenerFanOut] = Field(default_factory=list)


class GetRequestFlowOutput(ToolOutput):
    """Full multi-hop trace for one route.

    Structurally this is :class:`TraceRouteOutput` plus the
    ``event_chain`` fan-out.
    """

    route_id: str | None = None
    uri: str | None = None
    methods: list[str] = Field(default_factory=list)
    name: str | None = None
    handler: RouteTraceHandler | None = None
    middleware: list[str] = Field(default_factory=list)
    form_request: str | None = None
    fires_events: list[str] = Field(default_factory=list)
    dispatches_jobs: list[str] = Field(default_factory=list)
    sends_notifications: list[str] = Field(
        default_factory=list,
        description=(
            "FQNs of Notification classes the handler sends. "
            "Separate from ``dispatches_jobs`` so an agent doesn't "
            "have to filter dispatches by class kind."
        ),
    )
    policies: list[str] = Field(default_factory=list)
    event_chain: list[EventFanOut] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = (
        "middleware",
        "fires_events",
        "dispatches_jobs",
        "sends_notifications",
        "policies",
        "event_chain",
    )


class GetRequestFlowTool:
    """Return a route's full handling trace plus the event fan-out."""

    name: ClassVar[str] = "get_request_flow"
    description: ClassVar[str] = (
        "Return the full request-handling flow for a route: middleware "
        "stack, controller handler, form request, events fired, jobs "
        "dispatched, policies, plus the listeners that respond to each "
        "event and their downstream events/jobs. "
        "**Arguments (either form):** "
        '``route_id`` (string, e.g. ``route_id="route:GET:/api/users"``) '
        "OR ``method`` + ``uri`` together "
        '(e.g. ``method="GET", uri="/api/users"``). '
        "Use this when you want a single-shot answer to 'what happens "
        "when this URL is hit?'. For just the handler chain without "
        "the event fan-out, use ``trace_route``. Note: ``fires_events`` "
        "and ``dispatches_jobs`` only show *direct* dispatches from the "
        "controller method; the transitive call-graph (controller → "
        "service → event) requires ``response.coverage.calls_indexed="
        "true`` (project indexed with an LSP)."
    )
    input_model: ClassVar[type[ToolInput]] = GetRequestFlowInput
    output_model: ClassVar[type[ToolOutput]] = GetRequestFlowOutput
    latency_budget_ms: ClassVar[int] = 300

    def execute(
        self,
        payload: GetRequestFlowInput,
        ctx: QueryContext,
    ) -> GetRequestFlowOutput:
        """Walk the route → handler → events → listeners chain."""
        graph = ctx.storage.graph().load()

        # Reuse the trace_route resolver via a compatible shim.
        trace_payload = TraceRouteInput(
            route_id=payload.route_id,
            method=payload.method,
            uri=payload.uri,
        )
        route_node = _resolve_route(graph, trace_payload)
        if route_node is None:
            return GetRequestFlowOutput(
                error=_resolve_error_message(trace_payload),
                error_code="route_not_found",
            )

        return build_request_flow(graph, route_node)


def build_request_flow(graph: Graph, route_node: Node) -> GetRequestFlowOutput:
    """Build a :class:`GetRequestFlowOutput` for a resolved route node.

    Shared by :class:`GetRequestFlowTool` and :class:`DescribeFlowTool`
    (the latter does fuzzy resolution but reuses this terminal walk).
    """
    attrs = route_node.attributes
    middleware = _collect_middleware(graph, route_node.id)
    handler, handler_method_id = _resolve_handler(graph, route_node.id)

    fires: list[str] = []
    dispatches: list[str] = []
    notifies: list[str] = []
    form_request: str | None = None
    event_chain: list[EventFanOut] = []

    if handler_method_id is not None:
        fires = _collect_targets(graph, handler_method_id, EdgeKind.FIRES)
        dispatches = _collect_targets(graph, handler_method_id, EdgeKind.DISPATCHES)
        notifies = _collect_targets(graph, handler_method_id, EdgeKind.NOTIFIES)
        form_request = _resolve_form_request(graph, handler_method_id)
        event_chain = _build_event_chain(graph, handler_method_id)

    policies: list[str] = []
    if handler is not None and handler.class_fqn is not None:
        policies = _collect_policies(graph, handler.class_fqn)

    return GetRequestFlowOutput(
        route_id=route_node.id,
        uri=str_attr(attrs, "uri") or route_node.name,
        methods=str_list_attr(attrs, "methods"),
        name=str_attr(attrs, "name"),
        handler=handler,
        middleware=middleware,
        form_request=form_request,
        fires_events=sorted(set(fires)),
        dispatches_jobs=sorted(set(dispatches)),
        sends_notifications=sorted(set(notifies)),
        policies=sorted(set(policies)),
        event_chain=event_chain,
    )


def _build_event_chain(graph: Graph, handler_method_id: str) -> list[EventFanOut]:
    """For each event the handler fires, list its listeners + downstream."""
    chain: list[EventFanOut] = []
    seen_events: set[str] = set()

    for edge in graph.edges:
        if edge.source != handler_method_id or edge.kind != EdgeKind.FIRES:
            continue
        event_node = graph.node_by_id(edge.target)
        if event_node is None or event_node.id in seen_events:
            continue
        seen_events.add(event_node.id)

        listeners: list[ListenerFanOut] = []
        for listener_edge in incoming(graph, event_node.id, EdgeKind.LISTENS_TO):
            listener_node = graph.node_by_id(listener_edge.source)
            if listener_node is None:
                continue
            downstream_fires = _collect_targets(
                graph,
                listener_node.id,
                EdgeKind.FIRES,
            )
            downstream_dispatches = _collect_targets(
                graph,
                listener_node.id,
                EdgeKind.DISPATCHES,
            )
            downstream_notifies = _collect_targets(
                graph,
                listener_node.id,
                EdgeKind.NOTIFIES,
            )
            listeners.append(
                ListenerFanOut(
                    listener=listener_node.name,
                    listener_fqn=str_attr(listener_node.attributes, "fqn"),
                    fires=sorted(set(downstream_fires)),
                    dispatches=sorted(set(downstream_dispatches)),
                    notifies=sorted(set(downstream_notifies)),
                ),
            )

        listeners.sort(key=lambda entry: entry.listener)
        chain.append(
            EventFanOut(
                event=event_node.name,
                listeners=listeners,
            ),
        )

    chain.sort(key=lambda e: e.event)
    return chain
