"""``find_callers`` — reverse call graph lookup.

Given a method FQN (``Class::method``), walk ``CALLS`` edges
backwards to find every method that calls it. The ``CALLS``
edges are populated by Phase 3's LSP-driven static analyser;
until the analyser lands, this tool returns empty results on
fixtures that predate it — tests should be written against the
contract (shape + ordering), not the specific caller counts.

Each row identifies the calling method's class, method name,
and the file/line where the call happens (stored as an edge
attribute by the static analyser).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import int_attr, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.query.context import QueryContext


class FindCallersInput(ToolInput):
    """Identify the method whose callers we want."""

    method_fqn: str = Field(
        description=(
            "Method identifier in ``ClassFQN::methodName`` form, or the "
            "stable graph id (``method:<class_fqn>::<name>``)."
        ),
    )


class CallerRow(ToolOutput):
    """One caller site for the target method."""

    class_fqn: str | None = None
    method: str
    file: str | None = None
    line: int | None = None


class FindCallersOutput(ToolOutput):
    """Container for caller rows."""

    method_fqn: str | None = None
    total: int = 0
    returned: int = 0
    callers: list[CallerRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("callers",)


class FindCallersTool:
    """Return every method that calls a specific target method."""

    name: ClassVar[str] = "find_callers"
    description: ClassVar[str] = (
        "Walk ``CALLS`` edges backwards and return every method that "
        "invokes the target. "
        "**Argument:** ``method_fqn`` in ``ClassFQN::methodName`` form "
        '(example: ``method_fqn="App\\\\Models\\\\User::scopeActive"``). '
        "The stable graph id form ``method:<class_fqn>::<name>`` is "
        "also accepted. Call-site file and line come from the edge "
        "attributes populated by the LSP-driven static analyser. "
        "**Returns an empty list unless the index was built with an "
        "LSP server** — check ``response.coverage.calls_indexed`` "
        "before treating an empty result as 'no callers'."
    )
    input_model: ClassVar[type[ToolInput]] = FindCallersInput
    output_model: ClassVar[type[ToolOutput]] = FindCallersOutput
    latency_budget_ms: ClassVar[int] = 300

    def execute(
        self,
        payload: FindCallersInput,
        ctx: QueryContext,
    ) -> FindCallersOutput:
        """Walk ``CALLS`` edges backwards from the target method."""
        graph = ctx.storage.graph().load()

        method_id = _resolve_method_id(graph, payload.method_fqn)
        if method_id is None:
            return FindCallersOutput(
                method_fqn=payload.method_fqn,
                error=f"No method found matching {payload.method_fqn!r}.",
                error_code="method_not_found",
            )

        # The method exists, but if the index was built without an LSP
        # the CALLS edges were never populated. Return a structured
        # signal rather than an empty result the agent can't distinguish
        # from "this method genuinely has no callers".
        if ctx.coverage is not None and ctx.coverage.calls_indexed is False:
            return FindCallersOutput(
                method_fqn=method_id,
                error=(
                    "Callers cannot be resolved: this index was built without "
                    "an LSP server, so CALLS edges were never populated. "
                    "Re-index with ``--lsp auto`` (or ``--lsp intelephense``) "
                    "to enable. ``response.coverage.calls_indexed`` is the "
                    "canary."
                ),
                error_code="calls_not_indexed",
            )

        rows: list[CallerRow] = []
        for edge in incoming(graph, method_id, EdgeKind.CALLS):
            caller = graph.node_by_id(edge.source)
            if caller is None:
                continue

            # File/line come from the edge attributes (the call site),
            # but fall back to the caller method's definition if the
            # analyser didn't record them.
            edge_attrs = dict(edge.attributes)
            caller_attrs = caller.attributes
            rows.append(
                CallerRow(
                    class_fqn=str_attr(caller_attrs, "class_fqn"),
                    method=caller.name,
                    file=str_attr(edge_attrs, "file") or str_attr(caller_attrs, "file"),
                    line=int_attr(edge_attrs, "line") or int_attr(caller_attrs, "line"),
                ),
            )

        rows.sort(key=lambda r: (r.class_fqn or "", r.method, r.line or 0))

        return FindCallersOutput(
            method_fqn=method_id,
            total=len(rows),
            returned=len(rows),
            callers=rows,
        )


def _resolve_method_id(graph: Graph, query: str) -> str | None:
    if query.startswith("method:"):
        return query if graph.node_by_id(query) is not None else None
    candidate = f"method:{query}"
    if graph.node_by_id(candidate) is not None:
        return candidate
    # Last resort: scan for a method whose full identifier matches.
    for node in graph.nodes:
        if node.kind != NodeKind.CONTROLLER_METHOD:
            continue
        class_fqn = str_attr(node.attributes, "class_fqn")
        if class_fqn is not None and f"{class_fqn}::{node.name}" == query:
            return node.id
    return None
