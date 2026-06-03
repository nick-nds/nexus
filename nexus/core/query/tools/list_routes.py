"""``list_routes`` - enumerate HTTP routes with optional filters.

The simplest structural tool. Exists for two reasons:

1. It's the agent's go-to first question on an unfamiliar project
   ("show me the routes").
2. It proves the registry + engine + response-budget pipeline end
   to end with a trivial implementation.

The tool reads route nodes directly from the persisted graph and
applies in-memory filters. Filtering server-side via SQL would be
marginally faster but would couple the tool to the specific
``SqliteGraphStore`` implementation; today's in-memory scan runs
in ~1 ms on a 1400-route project.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import (
    route_summary,
    str_attr,
    str_list_attr,
    uri_glob_matches,
)

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class ListRoutesInput(ToolInput):
    """Optional filters narrowing the set of routes returned.

    All filters are combined with AND. Unset filters mean "any".
    """

    method: str | None = Field(
        default=None,
        description="Exact HTTP verb to match (GET, POST, ...). Case-insensitive.",
    )
    uri_glob: str | None = Field(
        default=None,
        description=(
            "Shell-style glob pattern matched against the route's URI "
            "(``/api/v1/*``). Case-sensitive. The leading slash is "
            "optional - ``api/v1/*`` matches the same routes."
        ),
    )
    name_glob: str | None = Field(
        default=None,
        description="Shell-style glob matched against the route's name.",
    )
    middleware: str | None = Field(
        default=None,
        description="Only routes that attach this middleware alias.",
    )


class RouteSummary(ToolOutput):
    """Compact view of a single route in the list response."""

    id: str
    uri: str
    methods: list[str]
    name: str | None
    controller: str | None
    method_name: str | None = Field(default=None, alias="method")
    action_kind: str
    middleware: list[str]


class ListRoutesOutput(ToolOutput):
    """Container for the filtered route list.

    The ``truncated`` + ``truncated_lists`` fields are used by the
    :class:`~nexus.core.query.ResponseBudget` when ``routes`` is
    longer than ``max_list_items``.
    """

    total: int = Field(description="Total route count before truncation.")
    returned: int = Field(description="Number of routes actually in ``routes``.")
    routes: list[RouteSummary] = Field(default_factory=list)
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("routes",)


class ListRoutesTool:
    """List all routes, optionally filtered by method / URI glob / middleware."""

    name: ClassVar[str] = "list_routes"
    description: ClassVar[str] = (
        "List the application's HTTP routes, optionally filtered by HTTP verb, "
        "URI glob, route name glob, or middleware alias. Use this as the first "
        "question on a new project to discover the request surface."
    )
    input_model: ClassVar[type[ToolInput]] = ListRoutesInput
    output_model: ClassVar[type[ToolOutput]] = ListRoutesOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: ListRoutesInput,
        ctx: QueryContext,
    ) -> ListRoutesOutput:
        """Return all matching routes as summaries."""
        graph = ctx.storage.graph().load()

        method_filter = payload.method.upper() if payload.method else None
        matches: list[RouteSummary] = []

        for node in graph.nodes:
            if node.kind != NodeKind.ROUTE:
                continue

            methods = str_list_attr(node.attributes, "methods")
            uri = str_attr(node.attributes, "uri") or node.name
            name_str = str_attr(node.attributes, "name")

            if method_filter is not None and method_filter not in methods:
                continue
            if payload.uri_glob is not None and not uri_glob_matches(uri, payload.uri_glob):
                continue
            if payload.name_glob is not None and (
                name_str is None or not fnmatch.fnmatchcase(name_str, payload.name_glob)
            ):
                continue

            summary = route_summary(node, graph)

            if payload.middleware is not None and payload.middleware not in summary.middleware:
                continue

            matches.append(summary)

        matches.sort(key=lambda r: (r.uri, r.methods[0] if r.methods else ""))

        return ListRoutesOutput(
            total=len(matches),
            returned=len(matches),
            routes=matches,
        )
