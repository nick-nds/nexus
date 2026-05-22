r"""``describe_module`` — drill into one module discovered by ``list_modules``.

Given a namespace prefix, summarise everything in that module:

* total class count and breakdown by kind
* immediate sub-namespaces (so the agent knows whether to drill
  further or pick a kind to enumerate)
* routes whose handler class lives in this module
* a small sample of class FQNs at each kind so the agent can
  pivot directly to ``describe_class`` / ``get_model_context``

This is the natural follow-up to ``list_modules``: the agent picks
a module from that response and asks "what's in there".
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr, str_list_attr
from nexus.core.query.tools.list_modules import (
    MODULE_CLASS_KINDS,
    class_fqn_for,
)
from nexus.core.query.traversal import outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.query.context import QueryContext


# Default cap on how many sample FQNs we surface per kind.  Enough
# to give the agent a feel for the module without flooding the
# response — the agent can call ``list_by_kind`` with a
# ``namespace_prefix`` filter to enumerate fully.
_DEFAULT_SAMPLE_PER_KIND = 5


class DescribeModuleInput(ToolInput):
    """Identify the module to summarise."""

    prefix: str = Field(
        min_length=1,
        description=(
            "Namespace prefix of the module — typically a value the "
            "agent saw on a ``list_modules`` response (e.g. "
            "``App\\Modules\\CRM``). FQN matching is exact-prefix; "
            "trailing backslashes are ignored."
        ),
    )
    sample_per_kind: int = Field(
        default=_DEFAULT_SAMPLE_PER_KIND,
        ge=1,
        le=50,
        description=(
            "How many representative class FQNs to surface per kind. "
            "The agent can drill into a kind via ``list_by_kind`` if "
            "this preview isn't enough."
        ),
    )


class ModuleClassSample(ToolOutput):
    """Per-kind sample for the module response."""

    kind: str
    total: int
    fqns: list[str] = Field(
        default_factory=list,
        description="Up to ``sample_per_kind`` FQNs in this kind, sorted ascending.",
    )


class ModuleRouteSummary(ToolOutput):
    """A route whose handler class lives in the module."""

    uri: str
    methods: list[str] = Field(default_factory=list)
    name: str | None = None
    handler_fqn: str | None = None


class DescribeModuleOutput(ToolOutput):
    """Module summary."""

    prefix: str | None = None
    total_classes: int = 0
    classes_by_kind: list[ModuleClassSample] = Field(default_factory=list)
    submodules: list[str] = Field(
        default_factory=list,
        description=(
            "Immediate child namespaces (one segment deeper than "
            "``prefix``). Empty when no classes nest beyond the "
            "prefix's depth."
        ),
    )
    routes: list[ModuleRouteSummary] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("classes_by_kind", "routes")


class DescribeModuleTool:
    """Summarise a module identified by namespace prefix."""

    name: ClassVar[str] = "describe_module"
    description: ClassVar[str] = (
        "Summarise one module: total class count, breakdown by kind, "
        "immediate sub-namespaces, and the routes whose handlers live "
        "inside the module. "
        "**Argument:** ``prefix`` (string) — the namespace prefix from "
        '``list_modules``, e.g. ``prefix="App\\\\Modules\\\\CRM"``. '
        "**Optional:** ``sample_per_kind`` (int, default 5, max 50) — "
        "FQN sample size per kind. Pair with ``list_modules`` for "
        "discovery — the agent picks a prefix from there and asks "
        'what\'s inside. Returns ``error_code: "empty_module"`` when '
        "no classes match the prefix so the agent can broaden."
    )
    input_model: ClassVar[type[ToolInput]] = DescribeModuleInput
    output_model: ClassVar[type[ToolOutput]] = DescribeModuleOutput
    latency_budget_ms: ClassVar[int] = 250

    def execute(
        self,
        payload: DescribeModuleInput,
        ctx: QueryContext,
    ) -> DescribeModuleOutput:
        """Walk every class, route, and sub-namespace under ``prefix``."""
        graph = ctx.storage.graph().load()
        prefix = payload.prefix.rstrip("\\")

        kind_counts: Counter[str] = Counter()
        kind_fqns: defaultdict[str, list[str]] = defaultdict(list)
        submodules: set[str] = set()

        for node in graph.nodes:
            if node.kind not in MODULE_CLASS_KINDS:
                continue
            fqn = class_fqn_for(node)
            if fqn is None or not _under_prefix(fqn, prefix):
                continue
            kind_counts[node.kind.value] += 1
            kind_fqns[node.kind.value].append(fqn)

            sub = _next_segment(fqn, prefix)
            if sub is not None:
                submodules.add(f"{prefix}\\{sub}")

        if not kind_counts:
            return DescribeModuleOutput(
                prefix=prefix,
                error=(
                    f"No classes found under {prefix!r}. Use "
                    f"``list_modules`` to see the available prefixes."
                ),
                error_code="empty_module",
            )

        classes_by_kind = _build_kind_samples(
            kind_counts,
            kind_fqns,
            sample_per_kind=payload.sample_per_kind,
        )
        routes = _collect_module_routes(graph, prefix=prefix)

        return DescribeModuleOutput(
            prefix=prefix,
            total_classes=sum(kind_counts.values()),
            classes_by_kind=classes_by_kind,
            submodules=sorted(submodules),
            routes=routes,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _under_prefix(fqn: str, prefix: str) -> bool:
    """True if ``fqn`` lives under ``prefix`` (strict — same prefix isn't a member)."""
    return fqn.startswith(prefix + "\\")


def _next_segment(fqn: str, prefix: str) -> str | None:
    """Return the immediate child namespace segment, or ``None`` if leaf-level."""
    rest = fqn[len(prefix) + 1 :]
    if "\\" not in rest:
        return None  # ``rest`` is the class itself
    return rest.split("\\", 1)[0]


def _build_kind_samples(
    counts: Counter[str],
    fqns: defaultdict[str, list[str]],
    *,
    sample_per_kind: int,
) -> list[ModuleClassSample]:
    """Sort each kind's FQN sample and assemble the response rows."""
    samples: list[ModuleClassSample] = []
    for kind, total in counts.items():
        sorted_fqns = sorted(fqns[kind])
        samples.append(
            ModuleClassSample(
                kind=kind,
                total=total,
                fqns=sorted_fqns[:sample_per_kind],
            ),
        )
    samples.sort(key=lambda s: (-s.total, s.kind))
    return samples


def _collect_module_routes(
    graph: Graph,
    *,
    prefix: str,
) -> list[ModuleRouteSummary]:
    """Find every route whose handler class FQN starts with ``prefix``."""
    routes: list[ModuleRouteSummary] = []
    for node in graph.nodes:
        if node.kind != NodeKind.ROUTE:
            continue
        handler_fqn = _route_handler_fqn(graph, node.id)
        if handler_fqn is None or not _under_prefix(handler_fqn, prefix):
            continue
        attrs = node.attributes
        routes.append(
            ModuleRouteSummary(
                uri=str_attr(attrs, "uri") or node.name,
                methods=str_list_attr(attrs, "methods"),
                name=str_attr(attrs, "name"),
                handler_fqn=handler_fqn,
            ),
        )
    routes.sort(key=lambda r: r.uri)
    return routes


def _route_handler_fqn(graph: Graph, route_id: str) -> str | None:
    for edge in outgoing(graph, route_id, EdgeKind.ROUTES_TO):
        method_node = graph.node_by_id(edge.target)
        if method_node is None:
            continue
        return str_attr(method_node.attributes, "class_fqn")
    return None
