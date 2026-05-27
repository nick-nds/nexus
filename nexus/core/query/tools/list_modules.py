r"""``list_modules`` - discover module-shaped namespace prefixes.

DDD codebases tend to organise classes into modules: ``App\Modules\
CRM``, ``App\Modules\Operations``, etc. Standard Laravel projects
have ``App\Models``, ``App\Http``, ``App\Jobs`` instead. This tool
detects whichever shape a project uses and returns a count of
classes per module so an agent can pick a section to explore.

Detection heuristic
===================

For each class FQN, we compute its *module prefix* as follows:

1. If the FQN contains a ``Modules`` (or ``Module``) segment, the
   prefix is everything up to and including the segment immediately
   after it. E.g.
   ``App\Modules\CRM\Customers\Customer`` → ``App\Modules\CRM``.
2. Otherwise the prefix is the first two namespace segments -
   e.g. ``App\Models\User`` → ``App\Models``.
3. Classes with fewer than two segments are skipped (top-level
   classes don't belong to any module).

This works for both standard Laravel and DDD-style codebases without
configuration.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr

if TYPE_CHECKING:
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


_MODULE_SEGMENT_NAMES: frozenset[str] = frozenset({"Modules", "Module"})

# Minimum namespace depth (segments) for a class to belong to any
# module - a top-level class like ``RootClass`` has no module.
_MIN_NAMESPACE_DEPTH = 2

# Class-shaped kinds that count toward module membership. Includes
# every kind whose nodes have ``class:<fqn>`` ids - middleware,
# resources, observers, etc. all belong to user-authored modules.
# Excludes graph-level alias nodes (``middleware:auth``,
# ``route:GET /…``, ``scheduled_task:…``) which don't carry a class
# FQN; those are filtered out anyway by :func:`class_fqn_for` returning
# ``None`` for them. Exposed publicly so ``describe_module`` uses the
# exact same set.
MODULE_CLASS_KINDS: frozenset[NodeKind] = frozenset(
    {
        NodeKind.CONTROLLER,
        NodeKind.MODEL,
        NodeKind.EVENT,
        NodeKind.LISTENER,
        NodeKind.JOB,
        NodeKind.NOTIFICATION,
        NodeKind.MAILABLE,
        NodeKind.POLICY,
        NodeKind.FORM_REQUEST,
        NodeKind.OBSERVER,
        NodeKind.RESOURCE,
        NodeKind.COMMAND,
        NodeKind.SERVICE_PROVIDER,
        NodeKind.CAST,
        NodeKind.MIDDLEWARE,
        NodeKind.INTERFACE,
        NodeKind.ENUM,
        NodeKind.TRAIT,
        NodeKind.BOOTSTRAP,
        NodeKind.CLASS,
    },
)


class ListModulesInput(ToolInput):
    """Filter parameters for module discovery."""

    min_classes: int = Field(
        default=2,
        ge=1,
        le=1000,
        description=(
            "Hide modules with fewer than this many classes. "
            "Filters out one-off prefixes from the response."
        ),
    )


class ModuleSummary(ToolOutput):
    """One detected module."""

    prefix: str = Field(description="Namespace prefix (``App\\Modules\\CRM``).")
    class_count: int
    kinds: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Map of NodeKind value (``model``, ``controller``, …) "
            "to the number of classes of that kind in this module."
        ),
    )


class ListModulesOutput(ToolOutput):
    """Container for the discovered modules."""

    total: int = 0
    returned: int = 0
    modules: list[ModuleSummary] = Field(default_factory=list)
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("modules",)


class ListModulesTool:
    """Discover module-shaped namespace prefixes in the project."""

    name: ClassVar[str] = "list_modules"
    description: ClassVar[str] = (
        "Discover the project's modules by grouping classes by "
        "namespace prefix. DDD-style codebases get one entry per "
        "``App\\Modules\\X``; standard Laravel projects get one per "
        "``App\\Y`` (Models, Http, Jobs, …). Use as the first step "
        "on a large unfamiliar codebase, then drill in with "
        "``describe_module(prefix)`` or ``list_by_kind`` filtered "
        "by ``namespace_prefix``."
    )
    input_model: ClassVar[type[ToolInput]] = ListModulesInput
    output_model: ClassVar[type[ToolOutput]] = ListModulesOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: ListModulesInput,
        ctx: QueryContext,
    ) -> ListModulesOutput:
        """Group every class node by its detected module prefix."""
        graph = ctx.storage.graph().load()

        per_module_kinds: dict[str, Counter[str]] = {}
        for node in graph.nodes:
            if node.kind not in MODULE_CLASS_KINDS:
                continue
            fqn = class_fqn_for(node)
            if fqn is None:
                continue
            prefix = detect_module_prefix(fqn)
            if prefix is None:
                continue
            per_module_kinds.setdefault(prefix, Counter())[node.kind.value] += 1

        modules: list[ModuleSummary] = []
        for prefix, counts in per_module_kinds.items():
            class_count = sum(counts.values())
            if class_count < payload.min_classes:
                continue
            modules.append(
                ModuleSummary(
                    prefix=prefix,
                    class_count=class_count,
                    kinds=dict(counts),
                ),
            )

        # Sort by class count desc (biggest module first), prefix asc as tiebreak.
        modules.sort(key=lambda m: (-m.class_count, m.prefix))

        return ListModulesOutput(
            total=len(modules),
            returned=len(modules),
            modules=modules,
        )


# ---------------------------------------------------------------------------
# Public helpers (shared with describe_module)
# ---------------------------------------------------------------------------


def detect_module_prefix(fqn: str) -> str | None:
    """Return the module prefix for ``fqn``, or ``None`` if too shallow.

    See the module docstring for the heuristic. Exposed publicly so
    :class:`DescribeModuleTool` can validate input prefixes the same
    way (same answer for the same FQN, regardless of caller).
    """
    parts = fqn.split("\\")
    if len(parts) < _MIN_NAMESPACE_DEPTH:
        return None
    # Look for a Modules / Module segment with at least one segment after it.
    for i, segment in enumerate(parts[:-1]):
        if segment in _MODULE_SEGMENT_NAMES and i + 1 < len(parts) - 1:
            # Take everything up to and including the segment AFTER ``Modules``.
            return "\\".join(parts[: i + 2])
    # Fallback: first two namespace segments.
    return "\\".join(parts[:_MIN_NAMESPACE_DEPTH])


def class_fqn_for(node: Node) -> str | None:
    """Resolve a class node's FQN, preferring the id suffix over attrs.

    Exposed publicly so ``describe_module`` reads FQNs the same way.
    """
    if node.id.startswith("class:"):
        return node.id[len("class:") :]
    return str_attr(node.attributes, "fqn")
