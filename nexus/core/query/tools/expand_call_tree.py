r"""``expand_call_tree`` - bounded BFS through ``CALLS`` edges.

Given a method, walk the call graph in either direction (callers
or callees) up to a configurable depth and return every method
reached, with the call site that brought us there. Complements
:class:`FindCallersTool` (which returns one hop only) by giving
agents a multi-hop view in a single call.

Like :class:`FindCallersTool`, this tool depends on the LSP-driven
static analyser populating ``CALLS`` edges. On indexes built without
an LSP the result is empty - agents should check
``response.coverage.calls_indexed`` before treating an empty
response as "no callers/callees".

Output shape
============

A flat list of :class:`CallTreeNode`, ordered by BFS visit order.
Each node carries:

* ``depth`` - 1 for direct neighbours of the root, 2 for
  neighbours-of-neighbours, etc.
* ``via_*`` - the parent method that brought us here (i.e. the
  calling method when ``direction=upstream``, the called method
  when ``direction=downstream``), so the agent can reconstruct
  the tree without a follow-up call.
* ``call_site_*`` - file/line of the actual call statement,
  recorded by the static analyser on the edge.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import int_attr, str_attr
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Edge
    from nexus.core.query.context import QueryContext


_DIRECTIONS: frozenset[str] = frozenset({"upstream", "downstream"})


class ExpandCallTreeInput(ToolInput):
    """Identify the root method and the walk parameters."""

    method_fqn: str = Field(
        description=(
            "Method identifier in ``ClassFQN::methodName`` form, or the "
            "stable graph id (``method:<class_fqn>::<name>``)."
        ),
    )
    direction: str = Field(
        default="downstream",
        description=(
            "``upstream`` walks ``CALLS`` edges backwards (who calls "
            "this, recursively). ``downstream`` walks them forwards "
            "(what does this call, recursively)."
        ),
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=10,
        description=(
            "Maximum hop distance from the root. Depth 1 = direct "
            "callers/callees, depth 2 = their callers/callees, etc."
        ),
    )
    max_nodes: int = Field(
        default=200,
        ge=1,
        le=1000,
        description=(
            "Hard cap on returned nodes. Hot service methods can have "
            "hundreds of callers - without a cap the agent gets a wall "
            "of methods that crowds the next tool call out of context."
        ),
    )


class CallTreeNode(ToolOutput):
    """One method reached during the walk."""

    class_fqn: str | None = None
    method: str
    file: str | None = None
    line: int | None = None
    depth: int = Field(
        description="Hops from the root method (1 = direct neighbour).",
    )
    via_class_fqn: str | None = Field(
        default=None,
        description=(
            "Class FQN of the method that brought us here - for "
            "``upstream`` walks this is the callee (the method we "
            "discovered as calling ours), for ``downstream`` walks "
            "this is the caller (the method that called us)."
        ),
    )
    via_method: str | None = None
    call_site_file: str | None = Field(
        default=None,
        description="File path of the actual call statement, if recorded.",
    )
    call_site_line: int | None = None


class ExpandCallTreeOutput(ToolOutput):
    """Container for the call-tree response."""

    method_fqn: str | None = None
    direction: str | None = None
    max_depth: int = 0
    total: int = 0
    returned: int = 0
    nodes: list[CallTreeNode] = Field(default_factory=list)
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)
    truncated_reason: str | None = Field(
        default=None,
        description=(
            "Why the walk stopped: ``max_nodes`` (cap hit), "
            "``max_depth`` (depth limit hit), or ``None`` if the "
            "tree was fully traversed."
        ),
    )
    error: str | None = None
    error_code: str | None = None

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("nodes",)


class ExpandCallTreeTool:
    """BFS through ``CALLS`` edges from a starting method."""

    name: ClassVar[str] = "expand_call_tree"
    description: ClassVar[str] = (
        "Bounded BFS through the call graph from one method. "
        "``direction=upstream`` walks ``CALLS`` edges backwards (who "
        "calls this method, recursively); ``downstream`` walks them "
        "forwards (what does this method call, recursively). Returns "
        "a flat list of methods with depth + parent + call-site so the "
        "agent can reconstruct the tree. Useful when ``find_callers`` "
        "or ``find_event_chains`` only give one hop and the agent "
        "needs the broader blast radius. **Returns an empty list "
        "unless the index was built with an LSP server** - check "
        "``response.coverage.calls_indexed`` before treating an empty "
        "result as 'no calls'."
    )
    input_model: ClassVar[type[ToolInput]] = ExpandCallTreeInput
    output_model: ClassVar[type[ToolOutput]] = ExpandCallTreeOutput
    latency_budget_ms: ClassVar[int] = 400

    def execute(
        self,
        payload: ExpandCallTreeInput,
        ctx: QueryContext,
    ) -> ExpandCallTreeOutput:
        """Resolve the root and walk ``CALLS`` edges to ``max_depth``."""
        direction = payload.direction.strip().lower()
        if direction not in _DIRECTIONS:
            return ExpandCallTreeOutput(
                method_fqn=payload.method_fqn,
                direction=payload.direction,
                max_depth=payload.max_depth,
                error=(
                    f"Unknown direction {payload.direction!r}. Valid "
                    f"values: {', '.join(sorted(_DIRECTIONS))}."
                ),
                error_code="invalid_direction",
            )

        graph = ctx.storage.graph().load()

        method_id = _resolve_method_id(graph, payload.method_fqn)
        if method_id is None:
            return ExpandCallTreeOutput(
                method_fqn=payload.method_fqn,
                direction=direction,
                max_depth=payload.max_depth,
                error=f"No method found matching {payload.method_fqn!r}.",
                error_code="method_not_found",
            )

        # The method exists, but if the index was built without an LSP
        # the CALLS edges were never populated. Return a structured
        # signal rather than an empty result the agent can't distinguish
        # from "this method genuinely has no callers/callees".
        if ctx.coverage is not None and ctx.coverage.calls_indexed is False:
            return ExpandCallTreeOutput(
                method_fqn=method_id,
                direction=direction,
                max_depth=payload.max_depth,
                error=(
                    "Call tree cannot be resolved: this index was built "
                    "without an LSP server, so CALLS edges were never "
                    "populated. Re-index with ``--lsp auto`` (or "
                    "``--lsp intelephense``) to enable. "
                    "``response.coverage.calls_indexed`` is the canary."
                ),
                error_code="calls_not_indexed",
            )

        nodes, truncated_reason = _walk(
            graph,
            start_id=method_id,
            direction=direction,
            max_depth=payload.max_depth,
            max_nodes=payload.max_nodes,
        )

        return ExpandCallTreeOutput(
            method_fqn=method_id,
            direction=direction,
            max_depth=payload.max_depth,
            total=len(nodes),
            returned=len(nodes),
            nodes=nodes,
            truncated=truncated_reason is not None,
            truncated_reason=truncated_reason,
        )


# ---------------------------------------------------------------------------
# BFS implementation
# ---------------------------------------------------------------------------


def _walk(
    graph: Graph,
    *,
    start_id: str,
    direction: str,
    max_depth: int,
    max_nodes: int,
) -> tuple[list[CallTreeNode], str | None]:
    """BFS from ``start_id`` along ``CALLS`` edges in ``direction``.

    Returns the visited nodes (excluding root) and a truncation
    reason (``None``, ``"max_nodes"``, or ``"max_depth"``).
    """
    visited: set[str] = {start_id}
    queue: deque[tuple[str, int]] = deque([(start_id, 0)])
    nodes: list[CallTreeNode] = []
    truncated_reason: str | None = None
    depth_limit_seen = False

    while queue:
        node_id, depth = queue.popleft()
        if depth >= max_depth:
            depth_limit_seen = True
            continue

        edges = _edges_for_direction(graph, node_id, direction)
        for edge in edges:
            neighbour_id, parent_id = _neighbour_and_parent(edge, direction)
            if neighbour_id in visited:
                continue
            visited.add(neighbour_id)

            neighbour_node = graph.node_by_id(neighbour_id)
            if neighbour_node is None:
                continue

            parent_node = graph.node_by_id(parent_id)
            edge_attrs = dict(edge.attributes)
            n_attrs = neighbour_node.attributes

            nodes.append(
                CallTreeNode(
                    class_fqn=str_attr(n_attrs, "class_fqn"),
                    method=neighbour_node.name,
                    file=str_attr(n_attrs, "file"),
                    line=int_attr(n_attrs, "line"),
                    depth=depth + 1,
                    via_class_fqn=(
                        str_attr(parent_node.attributes, "class_fqn")
                        if parent_node is not None
                        else None
                    ),
                    via_method=parent_node.name if parent_node is not None else None,
                    call_site_file=str_attr(edge_attrs, "file"),
                    call_site_line=int_attr(edge_attrs, "line"),
                ),
            )

            if len(nodes) >= max_nodes:
                truncated_reason = "max_nodes"
                return nodes, truncated_reason

            queue.append((neighbour_id, depth + 1))

    if (
        truncated_reason is None
        and depth_limit_seen
        and _has_unwalked_neighbours(graph, visited, direction)
    ):
        # Frontier still has neighbours we'd reach at a higher max_depth -
        # flag that for the agent so it can request more.
        truncated_reason = "max_depth"

    return nodes, truncated_reason


def _edges_for_direction(graph: Graph, node_id: str, direction: str) -> list[Edge]:
    if direction == "upstream":
        return incoming(graph, node_id, EdgeKind.CALLS)
    return outgoing(graph, node_id, EdgeKind.CALLS)


def _neighbour_and_parent(edge: Edge, direction: str) -> tuple[str, str]:
    """Return ``(neighbour_id, parent_id)`` for the BFS step.

    ``upstream``: the edge goes ``caller → callee``; we're walking
    backwards, so the neighbour is the source (caller) and the
    parent (the method we came from) is the target.

    ``downstream``: edge already points the right way; the neighbour
    is the target and the parent is the source.
    """
    if direction == "upstream":
        return edge.source, edge.target
    return edge.target, edge.source


def _has_unwalked_neighbours(graph: Graph, visited: set[str], direction: str) -> bool:
    """Did we leave any node unexplored on the depth-limit frontier?"""
    for node_id in visited:
        for edge in _edges_for_direction(graph, node_id, direction):
            neighbour_id, _ = _neighbour_and_parent(edge, direction)
            if neighbour_id not in visited:
                return True
    return False


# ---------------------------------------------------------------------------
# Method id resolution (mirrors find_callers)
# ---------------------------------------------------------------------------


def _resolve_method_id(graph: Graph, query: str) -> str | None:
    if query.startswith("method:"):
        return query if graph.node_by_id(query) is not None else None
    candidate = f"method:{query}"
    if graph.node_by_id(candidate) is not None:
        return candidate
    for node in graph.nodes:
        if node.kind != NodeKind.METHOD:
            continue
        class_fqn = str_attr(node.attributes, "class_fqn")
        if class_fqn is not None and f"{class_fqn}::{node.name}" == query:
            return node.id
    return None
