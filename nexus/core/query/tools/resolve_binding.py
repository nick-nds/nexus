"""``resolve_binding`` — look up the concrete class bound to an abstract.

Laravel's service container maps abstract names (interface FQNs,
strings, class names) to concrete implementations. Agents often
need to know "when the container resolves ``UserRepositoryInterface``,
which class does it actually hand back?".

This tool walks the ``BOUND_TO`` edge out of the binding node
and returns the concrete class FQN, the service provider file
where the binding lives, and whether the binding is shared
(singleton).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import bool_attr, int_attr, str_attr
from nexus.core.query.traversal import outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


class ResolveBindingInput(ToolInput):
    """Identify the abstract to resolve."""

    abstract: str = Field(
        description=(
            "Abstract identifier — interface FQN, class FQN, or any string the container was given."
        ),
    )


class ResolveBindingOutput(ToolOutput):
    """Concrete resolution of a container binding."""

    abstract: str | None = None
    concrete_class: str | None = None
    concrete_kind: str | None = Field(
        default=None,
        description="``class``, ``closure``, ``instance``, or ``alias``.",
    )
    shared: bool = False
    provider_file: str | None = None
    provider_line: int | None = None
    error: str | None = None
    error_code: str | None = None


class ResolveBindingTool:
    """Return the concrete class bound to an abstract in the container."""

    name: ClassVar[str] = "resolve_binding"
    description: ClassVar[str] = (
        "Given an abstract identifier, return the concrete class the "
        "container resolves it to, plus the service provider file and "
        "line where the binding was registered. "
        "**Argument:** ``abstract`` (string) — interface FQN, class "
        "FQN, or any string the container was given, e.g. "
        '``abstract="App\\\\Contracts\\\\PaymentGateway"`` or '
        '``abstract="cache"``. '
        "Handles class bindings, closure bindings, and instance "
        "bindings. Known limitation: closures with no return-type "
        "declaration cannot be statically resolved (tracked separately)."
    )
    input_model: ClassVar[type[ToolInput]] = ResolveBindingInput
    output_model: ClassVar[type[ToolOutput]] = ResolveBindingOutput
    latency_budget_ms: ClassVar[int] = 100

    def execute(
        self,
        payload: ResolveBindingInput,
        ctx: QueryContext,
    ) -> ResolveBindingOutput:
        """Look up the binding node and read its concrete attributes."""
        graph = ctx.storage.graph().load()

        binding_node = _resolve_binding_node(graph, payload.abstract)
        if binding_node is None:
            return ResolveBindingOutput(
                abstract=payload.abstract,
                error=f"No container binding found for {payload.abstract!r}.",
                error_code="binding_not_found",
            )

        attrs = binding_node.attributes
        concrete_class = str_attr(attrs, "concrete_class")

        # If the binding has an explicit ``BOUND_TO`` edge, prefer it —
        # it's the resolved class node which may be more canonical than
        # the raw attribute string.
        for edge in outgoing(graph, binding_node.id, EdgeKind.BOUND_TO):
            target = graph.node_by_id(edge.target)
            if target is not None:
                concrete_class = str_attr(target.attributes, "fqn") or target.name
                break

        return ResolveBindingOutput(
            abstract=binding_node.name,
            concrete_class=concrete_class,
            concrete_kind=str_attr(attrs, "concrete_kind"),
            shared=bool_attr(attrs, "shared"),
            provider_file=str_attr(attrs, "concrete_file"),
            provider_line=int_attr(attrs, "concrete_line"),
        )


def _resolve_binding_node(graph: Graph, query: str) -> Node | None:
    if query.startswith("binding:"):
        return graph.node_by_id(query)
    candidate = f"binding:{query}"
    node = graph.node_by_id(candidate)
    if node is not None:
        return node
    for n in graph.nodes:
        if n.kind == NodeKind.BINDING and n.name == query:
            return n
    return None
