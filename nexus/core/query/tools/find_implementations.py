"""``find_implementations`` - list every class deriving from a target.

Walks both ``IMPLEMENTS`` and ``EXTENDS`` edges backwards. Useful
for both "who implements ``RepositoryInterface``?" and "what
extends ``AcmeEvent``?" - agents don't need to know in advance
whether the target is an interface or an abstract class.

Audit P0-7: prior versions defaulted to ``IMPLEMENTS``-only, which
returned zero results for abstract-class targets. Abstract base
classes are the dominant Laravel-codebase abstraction (Module,
AcmeEvent, etc.), so the default flipped to inclusive. Set
``include_subclasses=false`` for the legacy IMPLEMENTS-only walk.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import resolve_class_id, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class FindImplementationsInput(ToolInput):
    """Identify the interface to search for implementers of."""

    interface_fqn: str = Field(
        description=(
            "Fully-qualified name of an interface OR a class - for "
            "interfaces the tool walks ``IMPLEMENTS``, for classes "
            "(abstract or concrete) it walks ``EXTENDS`` to enumerate "
            "subclasses."
        ),
    )
    include_subclasses: bool = Field(
        default=True,
        description=(
            "Walk ``EXTENDS`` edges in addition to ``IMPLEMENTS``. "
            "Default ``true`` (audit P0-7) so abstract-class targets "
            "return their subclasses without an opt-in. Set ``false`` "
            "to restrict to interface implementers only."
        ),
    )


class ImplementerRow(ToolOutput):
    """One class that implements or extends the target."""

    fqn: str
    short_name: str
    file: str | None = None
    via: str = Field(
        description="``implements`` or ``extends`` depending on how we reached it.",
    )


class FindImplementationsOutput(ToolOutput):
    """Container for the implementer rows."""

    interface_fqn: str | None = None
    total: int = 0
    returned: int = 0
    implementations: list[ImplementerRow] = Field(default_factory=list)
    warnings: list[str] = Field(
        default_factory=list,
        description=(
            "Non-fatal advisories about this response. Currently used "
            "to flag FQN case-corrections (audit P1-17)."
        ),
    )
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("implementations",)


class FindImplementationsTool:
    """Find every class that implements or extends a given type."""

    name: ClassVar[str] = "find_implementations"
    description: ClassVar[str] = (
        "Given an interface OR class FQN, return every class that "
        "derives from it. "
        "**Argument:** ``interface_fqn`` (string) - e.g. "
        '``interface_fqn="App\\\\Contracts\\\\PaymentGateway"`` for an '
        "interface, or "
        '``interface_fqn="App\\\\Modules\\\\Module"`` for an abstract '
        "base class. "
        "**Optional:** ``include_subclasses`` (bool, default **true** "
        "since audit P0-7) - walks ``EXTENDS`` edges in addition to "
        "``IMPLEMENTS``. Set ``false`` for interface-only behaviour. "
        'Each row carries ``via: "implements" | "extends"`` so the '
        "agent can tell which relationship was traversed."
    )
    input_model: ClassVar[type[ToolInput]] = FindImplementationsInput
    output_model: ClassVar[type[ToolOutput]] = FindImplementationsOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: FindImplementationsInput,
        ctx: QueryContext,
    ) -> FindImplementationsOutput:
        """Walk ``IMPLEMENTS`` (and optionally ``EXTENDS``) backwards."""
        graph = ctx.storage.graph().load()

        target_id, case_warning = resolve_class_id(graph, payload.interface_fqn)
        if target_id is None:
            return FindImplementationsOutput(
                interface_fqn=payload.interface_fqn,
                error=f"No class found with FQN {payload.interface_fqn!r}.",
                error_code="class_not_found",
            )

        warnings: list[str] = [case_warning] if case_warning is not None else []
        rows: list[ImplementerRow] = []
        for edge in incoming(graph, target_id, EdgeKind.IMPLEMENTS):
            source = graph.node_by_id(edge.source)
            if source is None:
                continue
            rows.append(_row(source, via="implements"))

        if payload.include_subclasses:
            for edge in incoming(graph, target_id, EdgeKind.EXTENDS):
                source = graph.node_by_id(edge.source)
                if source is None:
                    continue
                rows.append(_row(source, via="extends"))

        rows.sort(key=lambda r: r.fqn)

        return FindImplementationsOutput(
            interface_fqn=payload.interface_fqn,
            total=len(rows),
            returned=len(rows),
            implementations=rows,
            warnings=warnings,
        )


def _row(node: object, *, via: str) -> ImplementerRow:
    # `node` is a Node; written as `object` to keep the helper untyped
    # at the module boundary so mypy doesn't force a circular import.
    name = getattr(node, "name", "")
    attrs = getattr(node, "attributes", {})
    return ImplementerRow(
        fqn=str_attr(attrs, "fqn") or name,
        short_name=name,
        file=str_attr(attrs, "file"),
        via=via,
    )
