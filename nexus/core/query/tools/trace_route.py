"""``trace_route`` - single-hop trace of an HTTP route's handling.

Given a route (by id, or by ``(method, uri)``), return the full
request-handling trace: the ordered middleware stack, the
controller class + method (or closure marker), any form request
used for validation, events fired by the handler, jobs
dispatched, and any policy checks applied to the handler's
class.

This is the "what happens when this URL is hit?" tool. For the
deeper "and then what does that event trigger?" view, see
``get_request_flow``, which walks one more hop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import file_for_method_node, str_attr, str_list_attr
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


class TraceRouteInput(ToolInput):
    """Identify the route to trace.

    Either pass the route's stable ``route_id`` directly, or pass
    an HTTP verb + URI pair and the tool will look the route up.
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


class RouteTraceHandler(ToolOutput):
    """The controller class + method that handles the route."""

    class_fqn: str | None = None
    method_name: str | None = None
    action_kind: str = "unknown"
    file: str | None = None
    line: int | None = None


class TraceRouteOutput(ToolOutput):
    """Structured trace of one route.

    Every list is deduplicated and sorted so repeated queries
    return stable output - important for golden-file tests.
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
            "FQNs of Notification classes the handler dispatches via "
            "Laravel's notification facade or a Notifiable model. "
            "Separate from ``dispatches_jobs`` so agents can answer "
            "'what notifications does this send?' without filtering."
        ),
    )
    returns_views: list[str] = Field(
        default_factory=list,
        description=(
            "Blade view names the handler returns (e.g. ``auth.login``). "
            "Empty for API routes that return JSON. Lets an agent "
            "navigate to the actual presentation layer the route renders."
        ),
    )
    policies: list[str] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = (
        "middleware",
        "fires_events",
        "dispatches_jobs",
        "sends_notifications",
        "returns_views",
        "policies",
    )


class TraceRouteTool:
    """Return the middleware + handler + side-effects trace for a route."""

    name: ClassVar[str] = "trace_route"
    description: ClassVar[str] = (
        "Trace one HTTP route's handling: its middleware stack (in order), "
        "the controller class and method that handles it, any form request "
        "used for validation, events fired by the handler, jobs dispatched, "
        "and policies that apply to the handler's class. "
        "**Arguments (either form):** ``route_id`` (string, e.g. "
        '``route_id="route:GET:/api/users"``) OR ``method`` + ``uri`` '
        'together (e.g. ``method="GET", uri="/api/users"``). '
        "For the deeper event fan-out (listeners of each fired event + "
        "their downstream work), use ``get_request_flow``."
    )
    input_model: ClassVar[type[ToolInput]] = TraceRouteInput
    output_model: ClassVar[type[ToolOutput]] = TraceRouteOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: TraceRouteInput,
        ctx: QueryContext,
    ) -> TraceRouteOutput:
        """Resolve the route and walk its immediate neighbourhood."""
        graph = ctx.storage.graph().load()

        route_node = _resolve_route(graph, payload)
        if route_node is None:
            return TraceRouteOutput(
                error=_resolve_error_message(payload),
                error_code="route_not_found",
            )

        attrs = route_node.attributes
        middleware = _collect_middleware(graph, route_node.id)

        handler, handler_method_id = _resolve_handler(graph, route_node.id)

        fires: list[str] = []
        dispatches: list[str] = []
        notifies: list[str] = []
        views: list[str] = []
        form_request: str | None = None

        if handler_method_id is not None:
            fires = _collect_targets(graph, handler_method_id, EdgeKind.FIRES)
            dispatches = _collect_targets(graph, handler_method_id, EdgeKind.DISPATCHES)
            notifies = _collect_targets(graph, handler_method_id, EdgeKind.NOTIFIES)
            views = _collect_targets(graph, handler_method_id, EdgeKind.RETURNS_VIEW)
            form_request = _resolve_form_request(graph, handler_method_id)

        policies: list[str] = []
        if handler is not None and handler.class_fqn is not None:
            policies = _collect_policies(graph, handler.class_fqn)

        return TraceRouteOutput(
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
            returns_views=sorted(set(views)),
            policies=sorted(set(policies)),
        )


# ---------------------------------------------------------------------------
# Helpers (module-level so get_request_flow can reuse them)
# ---------------------------------------------------------------------------


def _resolve_route(graph: Graph, payload: TraceRouteInput) -> Node | None:
    """Resolve a route node from either an id or a ``(method, uri)`` pair."""
    if payload.route_id is not None:
        return graph.node_by_id(payload.route_id)

    if payload.uri is None:
        return None

    method_filter = payload.method.upper() if payload.method else None
    for node in graph.nodes:
        if node.kind != NodeKind.ROUTE:
            continue
        if str_attr(node.attributes, "uri") != payload.uri:
            continue
        if method_filter is not None:
            route_methods = str_list_attr(node.attributes, "methods")
            if method_filter not in route_methods:
                continue
        return node
    return None


def _resolve_error_message(payload: TraceRouteInput) -> str:
    if payload.route_id is not None:
        return f"No route found with id {payload.route_id!r}."
    if payload.method is not None and payload.uri is not None:
        return f"No route found matching {payload.method} {payload.uri}."
    if payload.uri is not None:
        return f"No route found with URI {payload.uri!r}."
    return "trace_route requires either ``route_id`` or ``uri``."


def _collect_middleware(graph: Graph, route_id: str) -> list[str]:
    """Return the middleware labels for a route in the order they appear.

    Some projects reference middleware by alias (``web``, ``api``) that
    never becomes a first-class node because no class backs it. In that
    case the edge exists but the target node is missing, so we fall back
    to the edge target's id tail - it's the alias the dev actually wrote.
    """
    names: list[str] = []
    for edge in outgoing(graph, route_id, EdgeKind.HAS_MIDDLEWARE):
        target = graph.node_by_id(edge.target)
        if target is not None:
            names.append(target.name)
            continue
        # Dangling target - use the id suffix (e.g. ``middleware:api`` → ``api``).
        _, _, suffix = edge.target.partition(":")
        names.append(suffix or edge.target)
    return names


def _resolve_handler(
    graph: Graph,
    route_id: str,
) -> tuple[RouteTraceHandler | None, str | None]:
    """Return the handler summary + the handler method's node id, if any."""
    routes_to = outgoing(graph, route_id, EdgeKind.ROUTES_TO)
    if not routes_to:
        route_node = graph.node_by_id(route_id)
        action_kind = str_attr(route_node.attributes, "action_kind") if route_node else None
        if action_kind:
            return RouteTraceHandler(action_kind=action_kind), None
        return None, None

    method_id = routes_to[0].target
    method_node = graph.node_by_id(method_id)
    if method_node is None:
        return None, None

    attrs = method_node.attributes
    handler = RouteTraceHandler(
        class_fqn=str_attr(attrs, "class_fqn"),
        method_name=method_node.name,
        action_kind="controller",
        # Method nodes don't carry ``file`` themselves - that's on the
        # parent class node. Resolve via class_fqn so the agent can
        # navigate without a follow-up ``describe_class`` call.
        file=file_for_method_node(graph, method_node),
        line=_int_or_none(attrs.get("line")),
    )
    return handler, method_id


def _collect_targets(graph: Graph, source_id: str, kind: EdgeKind) -> list[str]:
    names: list[str] = []
    for edge in outgoing(graph, source_id, kind):
        target = graph.node_by_id(edge.target)
        if target is not None:
            names.append(target.name)
    return names


def _resolve_form_request(graph: Graph, method_id: str) -> str | None:
    for edge in outgoing(graph, method_id, EdgeKind.VALIDATES_WITH):
        target = graph.node_by_id(edge.target)
        if target is not None:
            return str_attr(target.attributes, "fqn") or target.name
    return None


def _collect_policies(graph: Graph, class_fqn: str) -> list[str]:
    """Find policies whose ``APPLIES_TO`` edge points at ``class_fqn``."""
    from nexus.core.graph.ids import class_id  # noqa: PLC0415

    target_id = class_id(class_fqn)
    names: list[str] = []
    for edge in incoming(graph, target_id, EdgeKind.APPLIES_TO):
        source = graph.node_by_id(edge.source)
        if source is not None:
            names.append(source.name)
    return names


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
