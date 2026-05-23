"""``find_implementations`` — list the classes that implement an interface.

Takes an interface FQN and walks ``IMPLEMENTS`` edges backwards
to enumerate every class that declares it. Useful for "who
implements ``RepositoryInterface``?" questions and for
auditing an abstraction's real fan-out before a refactor.

Abstract parent classes can be asked about the same way via the
``include_abstract`` flag; when set, classes that ``EXTENDS`` the
target abstract are also included in the response.
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
        description="Fully-qualified interface name.",
    )
    include_subclasses: bool = Field(
        default=False,
        description=(
            "Also include classes that ``extends`` the target (treat the "
            "query as a super-type walk, not just interface implementers)."
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
        "Given an interface (or abstract class) FQN, return every class "
        "that implements it. "
        "**Argument:** ``interface_fqn`` (string) — e.g. "
        '``interface_fqn="App\\\\Contracts\\\\PaymentGateway"``. '
        "**Optional:** ``include_subclasses`` (bool, default false) — "
        "also walks ``EXTENDS`` edges so abstract-class subclasses appear."
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
