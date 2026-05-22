"""``describe_class`` — comprehensive view of one class.

The most commonly-useful tool for agents: given a fully-qualified
class name, return everything the graph knows about it —
inheritance, traits, interfaces, methods, direct neighbours,
related routes, fired events, dispatched jobs, and policies
targeting the class.

Design notes
============

* Input is the FQN, not the class short name. Short names are
  ambiguous in DDD projects with multiple ``UserController``s.
* Output is structured not prose — agents slice the fields they
  need. Prose rendering is Phase 5's job.
* Missing classes return an ``error`` field rather than raising.
  Agents handle structured "not found" better than exceptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from nexus.core.graph.ids import class_id
from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import (
    bool_attr,
    fqn_from_class_id,
    read_method_attributes,
    str_attr,
    str_list_attr,
)
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class DescribeClassInput(ToolInput):
    """Identify the class to describe."""

    fqn: str = Field(description="Fully-qualified class name, e.g. ``App\\Models\\User``.")


class MethodSummary(ToolOutput):
    """One method row inside a ``describe_class`` response."""

    node_id: str = Field(
        description=(
            "Graph node id (e.g. ``method:App\\Models\\User::scopeActive``). "
            "Pass directly to ``get_node_body`` without reconstructing — the "
            "kind prefix and the ``::`` separator are already in the right "
            "shape."
        ),
    )
    name: str
    visibility: str | None = None
    static: bool = False
    abstract: bool = False
    final: bool = False
    return_type: str | None = None
    line: int | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)


class RelatedRoute(ToolOutput):
    """A route that points at one of this class's methods."""

    uri: str
    methods: list[str]
    method_name: str | None = Field(default=None, alias="method")


class CacheKeyUsage(ToolOutput):
    """One cache key the class reads or writes."""

    key: str
    mode: str = Field(
        description=(
            "``read`` when only ``CACHE_READ`` edges exist, ``write`` "
            "when only ``CACHE_WRITE``, or ``both`` when methods on "
            "this class do both."
        ),
    )
    form: str | None = Field(
        default=None,
        description=(
            "``literal`` for static keys (``settings.timezone``) or "
            "``prefix`` for keys built from a literal + dynamic suffix "
            "(``user.{id}.session``). Falls back to ``None`` when the "
            "static analyser couldn't classify."
        ),
    )


class DescribeClassOutput(ToolOutput):
    """Full description of a class, or a structured ``not found`` error."""

    fqn: str | None = None
    short_name: str | None = None
    namespace: str | None = None
    file: str | None = None
    kind: str | None = None
    kinds: list[str] = Field(default_factory=list)
    abstract: bool | None = None
    final: bool | None = None
    parent: str | None = None
    interfaces: list[str] = Field(default_factory=list)
    traits: list[str] = Field(default_factory=list)
    methods: list[MethodSummary] = Field(default_factory=list)
    related_routes: list[RelatedRoute] = Field(default_factory=list)
    fires_events: list[str] = Field(default_factory=list)
    dispatches_jobs: list[str] = Field(default_factory=list)
    sends_notifications: list[str] = Field(default_factory=list)
    returns_views: list[str] = Field(default_factory=list)
    cache_keys: list[CacheKeyUsage] = Field(
        default_factory=list,
        description=(
            "Cache keys read or written by methods on this class. "
            "Populated from ``CACHE_READ`` / ``CACHE_WRITE`` edges. "
            "Pair with ``find_cache_users`` to discover the other end "
            "of the read/write relationship."
        ),
    )
    policies_applied_to: list[str] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = (
        "methods",
        "related_routes",
        "fires_events",
        "dispatches_jobs",
        "sends_notifications",
        "returns_views",
        "cache_keys",
    )


class DescribeClassTool:
    """Return a structured description of a class by fully-qualified name."""

    name: ClassVar[str] = "describe_class"
    description: ClassVar[str] = (
        "Return a comprehensive structured view of a class: kind, inheritance, "
        "methods, related routes, events fired by its methods, jobs dispatched, "
        "and any policy that targets it. "
        "**Argument:** ``fqn`` (string) — fully-qualified class name, e.g. "
        '``fqn="App\\\\Models\\\\User"``. '
        "Use this as the primary way to learn about a specific class. For "
        "Eloquent models, prefer ``get_model_context`` for a richer model-"
        "specific view."
    )
    input_model: ClassVar[type[ToolInput]] = DescribeClassInput
    output_model: ClassVar[type[ToolOutput]] = DescribeClassOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(  # noqa: PLR0912, PLR0915 — linear walk over several edge kinds; splitting hurts readability
        self,
        payload: DescribeClassInput,
        ctx: QueryContext,
    ) -> DescribeClassOutput:
        """Build the class description by walking the graph."""
        graph = ctx.storage.graph().load()
        node_id = class_id(payload.fqn)
        node = graph.node_by_id(node_id)

        if node is None:
            return DescribeClassOutput(
                error=f"No class found with FQN {payload.fqn!r}.",
                error_code="class_not_found",
            )

        attrs = node.attributes

        # Method nodes are linked to the class via PART_OF edges.
        method_ids: list[str] = [edge.source for edge in incoming(graph, node_id, EdgeKind.PART_OF)]
        methods: list[MethodSummary] = []
        for method_id in method_ids:
            method_node = graph.node_by_id(method_id)
            if method_node is None:
                continue
            info = read_method_attributes(method_node)
            methods.append(
                MethodSummary(
                    node_id=method_id,
                    name=info.name,
                    visibility=info.visibility,
                    static=info.static,
                    abstract=info.abstract,
                    final=info.final,
                    return_type=info.return_type,
                    line=info.line,
                    parameters=info.parameters,
                ),
            )
        methods.sort(key=lambda m: m.name)

        related_routes: list[RelatedRoute] = []
        for method_id in method_ids:
            for route_edge in incoming(graph, method_id, EdgeKind.ROUTES_TO):
                route_node = graph.node_by_id(route_edge.source)
                if route_node is None:
                    continue
                method_node = graph.node_by_id(method_id)
                related_routes.append(
                    RelatedRoute(
                        uri=str_attr(route_node.attributes, "uri") or route_node.name,
                        methods=str_list_attr(route_node.attributes, "methods"),
                        method=method_node.name if method_node is not None else None,
                    ),
                )

        fires_events: list[str] = []
        for method_id in method_ids:
            for edge in outgoing(graph, method_id, EdgeKind.FIRES):
                target = graph.node_by_id(edge.target)
                if target is not None:
                    fires_events.append(target.name)

        dispatches_jobs: list[str] = []
        sends_notifications: list[str] = []
        returns_views: list[str] = []
        cache_modes: dict[str, set[str]] = {}
        cache_forms: dict[str, str] = {}
        for method_id in method_ids:
            for edge in outgoing(graph, method_id, EdgeKind.DISPATCHES):
                target = graph.node_by_id(edge.target)
                if target is not None:
                    dispatches_jobs.append(target.name)
            for edge in outgoing(graph, method_id, EdgeKind.NOTIFIES):
                target = graph.node_by_id(edge.target)
                if target is not None:
                    sends_notifications.append(target.name)
            for edge in outgoing(graph, method_id, EdgeKind.RETURNS_VIEW):
                target = graph.node_by_id(edge.target)
                if target is not None:
                    returns_views.append(target.name)
            for edge in outgoing(graph, method_id, EdgeKind.CACHE_READ):
                _record_cache_usage(graph, edge, "read", cache_modes, cache_forms)
            for edge in outgoing(graph, method_id, EdgeKind.CACHE_WRITE):
                _record_cache_usage(graph, edge, "write", cache_modes, cache_forms)

        policies: list[str] = []
        for edge in incoming(graph, node_id, EdgeKind.APPLIES_TO):
            policy_node = graph.node_by_id(edge.source)
            if policy_node is not None:
                policies.append(policy_node.name)

        interfaces_list: list[str] = []
        for edge in outgoing(graph, node_id, EdgeKind.IMPLEMENTS):
            interfaces_list.append(fqn_from_class_id(graph, edge.target))

        traits_list: list[str] = []
        for edge in outgoing(graph, node_id, EdgeKind.USES_TRAIT):
            traits_list.append(fqn_from_class_id(graph, edge.target))

        parent_edges = outgoing(graph, node_id, EdgeKind.EXTENDS)
        parent_fqn: str | None = None
        if parent_edges:
            parent_node = graph.node_by_id(parent_edges[0].target)
            if parent_node is not None:
                parent_fqn = str_attr(parent_node.attributes, "fqn") or parent_node.name

        return DescribeClassOutput(
            fqn=str_attr(attrs, "fqn") or payload.fqn,
            short_name=node.name,
            namespace=str_attr(attrs, "namespace"),
            file=str_attr(attrs, "file"),
            kind=node.kind.value,
            kinds=str_list_attr(attrs, "kinds"),
            abstract=bool_attr(attrs, "abstract"),
            final=bool_attr(attrs, "final"),
            parent=parent_fqn,
            interfaces=sorted(set(interfaces_list)),
            traits=sorted(set(traits_list)),
            methods=methods,
            related_routes=related_routes,
            fires_events=sorted(set(fires_events)),
            dispatches_jobs=sorted(set(dispatches_jobs)),
            sends_notifications=sorted(set(sends_notifications)),
            returns_views=sorted(set(returns_views)),
            cache_keys=_build_cache_key_rows(cache_modes, cache_forms),
            policies_applied_to=sorted(set(policies)),
        )


def _record_cache_usage(
    graph: Any,
    edge: Any,
    mode: str,
    modes: dict[str, set[str]],
    forms: dict[str, str],
) -> None:
    """Aggregate a single CACHE_READ/CACHE_WRITE edge into the per-key tables."""
    target = graph.node_by_id(edge.target)
    if target is None:
        return
    key = target.name
    modes.setdefault(key, set()).add(mode)
    form = str_attr(dict(edge.attributes), "form")
    if form is not None and key not in forms:
        forms[key] = form


def _build_cache_key_rows(
    modes: dict[str, set[str]],
    forms: dict[str, str],
) -> list[CacheKeyUsage]:
    """Flatten the read/write tables into sorted :class:`CacheKeyUsage` rows."""
    rows: list[CacheKeyUsage] = []
    for key in sorted(modes):
        seen = modes[key]
        if seen == {"read", "write"}:
            mode = "both"
        elif seen == {"write"}:
            mode = "write"
        else:
            mode = "read"
        rows.append(CacheKeyUsage(key=key, mode=mode, form=forms.get(key)))
    return rows
