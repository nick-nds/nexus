"""``get_policy_for`` — resolve the policy applied to a model.

Laravel authorisation uses policy classes mapped to models (via
``AuthServiceProvider::$policies`` or convention). This tool
takes a model FQN and returns the policy class plus a list of
its ability methods (``view``, ``update``, etc.) so agents can
quickly answer "how is access to this model guarded?".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.ids import class_id
from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import read_method_attributes, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class GetPolicyForInput(ToolInput):
    """Identify the model to look up a policy for."""

    model_fqn: str = Field(
        description="Fully-qualified model class name, e.g. ``App\\Models\\Order``.",
    )


class PolicyMethod(ToolOutput):
    """One ability method on the policy class."""

    node_id: str = Field(
        description=(
            "Graph node id (e.g. "
            "``method:App\\Policies\\OrderPolicy::view``). Pass directly to "
            "``get_node_body`` to read the ability's source."
        ),
    )
    name: str
    visibility: str | None = None
    line: int | None = None


class GetPolicyForOutput(ToolOutput):
    """Policy class + methods, or structured error."""

    model_fqn: str | None = None
    policy_fqn: str | None = None
    policy_short_name: str | None = None
    file: str | None = None
    methods: list[PolicyMethod] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("methods",)


class GetPolicyForTool:
    """Return the policy class that authorises access to a model."""

    name: ClassVar[str] = "get_policy_for"
    description: ClassVar[str] = (
        "Given a model FQN, return the policy class registered against "
        "it — its FQN, source file, and ability methods (view, update, "
        "delete, etc.). "
        "**Argument:** ``model_fqn`` (string) — e.g. "
        '``model_fqn="App\\\\Models\\\\User"``. '
        "Returns a structured error if no policy is registered."
    )
    input_model: ClassVar[type[ToolInput]] = GetPolicyForInput
    output_model: ClassVar[type[ToolOutput]] = GetPolicyForOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: GetPolicyForInput,
        ctx: QueryContext,
    ) -> GetPolicyForOutput:
        """Resolve the policy via the ``APPLIES_TO`` edge."""
        graph = ctx.storage.graph().load()
        model_id = class_id(payload.model_fqn)
        if graph.node_by_id(model_id) is None:
            return GetPolicyForOutput(
                model_fqn=payload.model_fqn,
                error=f"No model found with FQN {payload.model_fqn!r}.",
                error_code="model_not_found",
            )

        policy_node = None
        for edge in incoming(graph, model_id, EdgeKind.APPLIES_TO):
            candidate = graph.node_by_id(edge.source)
            if candidate is not None:
                policy_node = candidate
                break

        if policy_node is None:
            return GetPolicyForOutput(
                model_fqn=payload.model_fqn,
                error=f"No policy is registered for {payload.model_fqn!r}.",
                error_code="policy_not_found",
            )

        method_rows: list[PolicyMethod] = []
        for edge in incoming(graph, policy_node.id, EdgeKind.PART_OF):
            method_node = graph.node_by_id(edge.source)
            if method_node is None:
                continue
            info = read_method_attributes(method_node)
            method_rows.append(
                PolicyMethod(
                    node_id=method_node.id,
                    name=info.name,
                    visibility=info.visibility,
                    line=info.line,
                ),
            )
        method_rows.sort(key=lambda m: m.name)

        return GetPolicyForOutput(
            model_fqn=payload.model_fqn,
            policy_fqn=str_attr(policy_node.attributes, "fqn") or policy_node.name,
            policy_short_name=policy_node.name,
            file=str_attr(policy_node.attributes, "file"),
            methods=method_rows,
        )
