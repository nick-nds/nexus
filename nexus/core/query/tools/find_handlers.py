"""``find_handlers`` — reverse lookup from URI or class to routes.

Reverse lookup from a URI glob or handler FQN to the routes that
target a given handler. Two common agent questions:

1. "Which class handles ``/api/users/{id}``?" — pass ``uri_glob``.
2. "Which routes point at ``ShowUserController``?" — pass
   ``handler_fqn`` (class FQN, optionally ``::method``).

The tool walks ``ROUTES_TO`` edges in the appropriate direction
and returns handler-focused rows: each row is one route + the
class/method/file/line that handles it. Middleware, events,
policies are deliberately omitted — use ``trace_route`` if you
need the full picture.
"""

from __future__ import annotations

import fnmatch
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


class FindHandlersInput(ToolInput):
    """Constraints on the handler search.

    At least one of ``uri_glob`` or ``handler_fqn`` must be set.
    Combining both narrows the result to routes that match both.
    """

    uri_glob: str | None = Field(
        default=None,
        description=("Shell-style glob matched against the route URI, e.g. ``/api/v1/users/*``."),
    )
    method: str | None = Field(
        default=None,
        description="Optional HTTP verb to filter by. Case-insensitive.",
    )
    handler_fqn: str | None = Field(
        default=None,
        description=(
            "Target class FQN (optionally ``Class::method``). Returns routes "
            "whose action resolves to this class / method."
        ),
    )


class HandlerRow(ToolOutput):
    """One (route, handler) pair in the response."""

    route_id: str
    uri: str
    methods: list[str]
    route_name: str | None = None
    action_kind: str
    class_fqn: str | None = None
    method_name: str | None = Field(default=None, alias="method")
    file: str | None = None
    line: int | None = None


class FindHandlersOutput(ToolOutput):
    """Container for the matched handler rows."""

    total: int = 0
    returned: int = 0
    handlers: list[HandlerRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("handlers",)


class FindHandlersTool:
    """Find the handler(s) responsible for routes matching a URI or class."""

    name: ClassVar[str] = "find_handlers"
    description: ClassVar[str] = (
        "Find the handler class + method for routes matching a URI glob "
        "and/or a handler FQN. "
        "**Arguments (all optional, but at least one required):** "
        '``uri_glob`` (e.g. ``uri_glob="/api/v1/users/*"``), '
        "``method`` (HTTP verb, case-insensitive), "
        "``handler_fqn`` (target class FQN, optionally ``Class::method``). "
        "Answers 'which class handles this URL?' or 'which routes point at "
        "this controller?'. Use ``trace_route`` for the full middleware + "
        "events trace of a single route."
    )
    input_model: ClassVar[type[ToolInput]] = FindHandlersInput
    output_model: ClassVar[type[ToolOutput]] = FindHandlersOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: FindHandlersInput,
        ctx: QueryContext,
    ) -> FindHandlersOutput:
        """Collect matching (route, handler) rows."""
        if payload.uri_glob is None and payload.handler_fqn is None:
            return FindHandlersOutput(
                error="find_handlers requires ``uri_glob`` or ``handler_fqn``.",
                error_code="missing_filter",
            )

        graph = ctx.storage.graph().load()
        method_filter = payload.method.upper() if payload.method else None

        candidate_routes = _candidate_routes(
            graph,
            handler_fqn=payload.handler_fqn,
        )

        rows: list[HandlerRow] = []
        for route_node in candidate_routes:
            uri = str_attr(route_node.attributes, "uri") or route_node.name
            if payload.uri_glob is not None and not fnmatch.fnmatchcase(
                uri,
                payload.uri_glob,
            ):
                continue

            methods = str_list_attr(route_node.attributes, "methods")
            if method_filter is not None and method_filter not in methods:
                continue

            row = _build_row(
                graph,
                route_node=route_node,
                uri=uri,
                methods=methods,
                required_method_name=_extract_method_name(payload.handler_fqn),
            )
            if row is None:
                continue
            rows.append(row)

        rows.sort(key=lambda r: (r.uri, r.methods[0] if r.methods else ""))

        return FindHandlersOutput(
            total=len(rows),
            returned=len(rows),
            handlers=rows,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _candidate_routes(graph: Graph, *, handler_fqn: str | None) -> list[Node]:
    """Narrow the route set by ``handler_fqn`` if possible.

    When an FQN is supplied we walk ``ROUTES_TO`` backwards from
    the class's method nodes so dense apps don't scan every route.
    """
    if handler_fqn is None:
        return [n for n in graph.nodes if n.kind == NodeKind.ROUTE]

    class_fqn, _ = _split_handler_fqn(handler_fqn)

    # Find method nodes whose class_fqn matches, then walk backwards.
    method_ids: list[str] = []
    for node in graph.nodes:
        if node.kind != NodeKind.METHOD:
            continue
        if str_attr(node.attributes, "class_fqn") == class_fqn:
            method_ids.append(node.id)

    if not method_ids:
        return []

    route_ids: set[str] = set()
    for method_id in method_ids:
        for edge in incoming(graph, method_id, EdgeKind.ROUTES_TO):
            route_ids.add(edge.source)

    return [n for n in graph.nodes if n.kind == NodeKind.ROUTE and n.id in route_ids]


def _build_row(
    graph: Graph,
    *,
    route_node: Node,
    uri: str,
    methods: list[str],
    required_method_name: str | None,
) -> HandlerRow | None:
    """Assemble a :class:`HandlerRow` for one route, or ``None`` to skip."""
    action_kind = str_attr(route_node.attributes, "action_kind") or "unknown"
    name = str_attr(route_node.attributes, "name")

    class_fqn: str | None = None
    method_name: str | None = None
    file: str | None = None
    line: int | None = None

    routes_to = outgoing(graph, route_node.id, EdgeKind.ROUTES_TO)
    if routes_to:
        target = graph.node_by_id(routes_to[0].target)
        if target is not None:
            class_fqn = str_attr(target.attributes, "class_fqn")
            method_name = target.name
            # Method nodes don't carry ``file`` — resolve via the
            # parent class node so agents don't have to round-trip.
            file = file_for_method_node(graph, target)
            raw_line = target.attributes.get("line")
            if isinstance(raw_line, int) and not isinstance(raw_line, bool):
                line = raw_line

    if required_method_name is not None and method_name != required_method_name:
        return None

    return HandlerRow(
        route_id=route_node.id,
        uri=uri,
        methods=methods,
        route_name=name,
        action_kind=action_kind,
        class_fqn=class_fqn,
        method=method_name,
        file=file,
        line=line,
    )


def _split_handler_fqn(handler_fqn: str) -> tuple[str, str | None]:
    if "::" in handler_fqn:
        class_fqn, method_name = handler_fqn.split("::", 1)
        return class_fqn, method_name
    return handler_fqn, None


def _extract_method_name(handler_fqn: str | None) -> str | None:
    if handler_fqn is None:
        return None
    _, method_name = _split_handler_fqn(handler_fqn)
    return method_name
