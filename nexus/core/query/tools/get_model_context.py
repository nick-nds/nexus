"""``get_model_context`` — model-specific view for Eloquent models.

A specialised variant of ``describe_class`` tuned for the Eloquent
question shape. Agents asking "what's on the User model" want to
see: traits (HasRoles, SoftDeletes), relationships, the policy
applying to the model, observers, routes whose controllers touch
it, and the model's methods grouped by convention (scopes,
accessors, relations).

The implementation reads the same graph as ``describe_class`` but
restricts the answer to model-relevant edges, so the response
stays dense and within the budget.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import Field

from nexus.core.graph.ids import class_id
from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import (
    fqn_from_class_id,
    read_method_attributes,
    str_attr,
    str_list_attr,
)
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class GetModelContextInput(ToolInput):
    """Identify the Eloquent model."""

    fqn: str = Field(description="Fully-qualified model FQN, e.g. ``App\\Models\\User``.")


class ModelMethod(ToolOutput):
    """One method row in the model-context response."""

    node_id: str = Field(
        description=(
            "Graph node id (e.g. "
            "``method:App\\Models\\User::scopeActive``). Pass directly to "
            "``get_node_body`` to read the method's source."
        ),
    )
    name: str
    visibility: str | None = None
    return_type: str | None = None
    line: int | None = None
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    category: str = Field(
        default="method",
        description="Loose classification: relation, scope, accessor, mutator, method.",
    )


class GetModelContextOutput(ToolOutput):
    """Eloquent-tuned view of a model class."""

    fqn: str | None = None
    short_name: str | None = None
    file: str | None = None
    traits: list[str] = Field(default_factory=list)
    interfaces: list[str] = Field(default_factory=list)
    parent: str | None = None
    is_model: bool = False
    policy: str | None = Field(
        default=None,
        description="FQN of the policy applied to this model, if any.",
    )
    observers: list[str] = Field(default_factory=list)
    methods: list[ModelMethod] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("methods",)


class GetModelContextTool:
    """Return Eloquent-flavoured context for a model class."""

    name: ClassVar[str] = "get_model_context"
    description: ClassVar[str] = (
        "Return a model-specific view of an Eloquent model: its traits, "
        "interfaces, the policy applied to it, observers, and methods "
        "categorised as relations / scopes / accessors. Prefer this over "
        "``describe_class`` when the class is a model."
    )
    input_model: ClassVar[type[ToolInput]] = GetModelContextInput
    output_model: ClassVar[type[ToolOutput]] = GetModelContextOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(  # noqa: PLR0912 - branchy aggregation across multiple edge types is clearer inline
        self,
        payload: GetModelContextInput,
        ctx: QueryContext,
    ) -> GetModelContextOutput:
        """Assemble the model context from the graph."""
        graph = ctx.storage.graph().load()
        node_id = class_id(payload.fqn)
        node = graph.node_by_id(node_id)

        if node is None:
            return GetModelContextOutput(
                error=f"No class found with FQN {payload.fqn!r}.",
                error_code="class_not_found",
            )

        attrs = node.attributes
        is_model = node.kind == NodeKind.MODEL or "model" in str_list_attr(attrs, "kinds")

        traits: list[str] = []
        for edge in outgoing(graph, node_id, EdgeKind.USES_TRAIT):
            traits.append(fqn_from_class_id(graph, edge.target))

        interfaces: list[str] = []
        for edge in outgoing(graph, node_id, EdgeKind.IMPLEMENTS):
            interfaces.append(fqn_from_class_id(graph, edge.target))

        parent_fqn: str | None = None
        parents = outgoing(graph, node_id, EdgeKind.EXTENDS)
        if parents:
            parent_node = graph.node_by_id(parents[0].target)
            if parent_node is not None:
                parent_fqn = str_attr(parent_node.attributes, "fqn") or parent_node.name

        # Policy is a single APPLIES_TO edge pointing at this class.
        policy: str | None = None
        for edge in incoming(graph, node_id, EdgeKind.APPLIES_TO):
            policy_node = graph.node_by_id(edge.source)
            if policy_node is not None:
                policy = policy_node.name
                break

        # Observers — precise reverse-traversal of OBSERVES edges
        # populated by ``ObserverRegistrationVisitor`` in PhaseC. Each
        # ``Model::observe(SomeObserver::class)`` call site produces
        # one inbound edge here. Falls back to the original heuristic
        # (observer class names containing the model's short name) for
        # projects that haven't been re-indexed since this edge type
        # was introduced.
        observers: list[str] = []
        for edge in incoming(graph, node_id, EdgeKind.OBSERVES):
            observer_node = graph.node_by_id(edge.source)
            if observer_node is not None:
                observer_fqn = str_attr(observer_node.attributes, "fqn") or observer_node.name
                observers.append(observer_fqn)
            else:
                observers.append(fqn_from_class_id(graph, edge.source))

        # Backstop: scan for observer-kinded classes referencing the
        # model's short name when no OBSERVES edges exist (older index).
        if not observers:
            short_name = node.name
            for other in graph.nodes:
                if other.kind != NodeKind.OBSERVER:
                    continue
                if short_name in other.id:
                    other_fqn = str_attr(other.attributes, "fqn") or other.name
                    observers.append(other_fqn)

        # Methods with loose categorisation.
        methods: list[ModelMethod] = []
        for edge in incoming(graph, node_id, EdgeKind.PART_OF):
            method_node = graph.node_by_id(edge.source)
            if method_node is None:
                continue
            info = read_method_attributes(method_node)
            category = _categorise_model_method(
                name=info.name,
                return_type=info.return_type or "",
            )
            methods.append(
                ModelMethod(
                    node_id=method_node.id,
                    name=info.name,
                    visibility=info.visibility,
                    return_type=info.return_type,
                    line=info.line,
                    parameters=info.parameters,
                    category=category,
                ),
            )
        methods.sort(key=lambda m: (m.category, m.name))

        return GetModelContextOutput(
            fqn=str_attr(attrs, "fqn") or payload.fqn,
            short_name=node.name,
            file=str_attr(attrs, "file"),
            traits=sorted(set(traits)),
            interfaces=sorted(set(interfaces)),
            parent=parent_fqn,
            is_model=is_model,
            policy=policy,
            observers=sorted(set(observers)),
            methods=methods,
        )


def _categorise_model_method(*, name: str, return_type: str) -> str:
    """Best-effort bucket for a model method.

    Uses Laravel-convention naming + return-type hints. The category
    is a hint to agents, not a contract.
    """
    if name.startswith("scope"):
        return "scope"
    if name.startswith("get") and name.endswith("Attribute"):
        return "accessor"
    if name.startswith("set") and name.endswith("Attribute"):
        return "mutator"
    relation_types = (
        "HasMany",
        "BelongsTo",
        "BelongsToMany",
        "HasOne",
        "MorphTo",
        "MorphMany",
        "MorphOne",
        "MorphToMany",
    )
    if any(rt in return_type for rt in relation_types):
        return "relation"
    return "method"
