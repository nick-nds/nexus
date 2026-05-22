"""``find_dispatchers`` — find the methods that fire a given event.

The mirror image of :mod:`find_listeners`: instead of "who reacts
to X", this tool answers "who causes X to be fired in the first
place?" by walking ``FIRES`` edges backwards from the event.

Each row identifies the firing method — its class FQN, method
name, file, and line — so agents can jump to the source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import int_attr, str_attr
from nexus.core.query.tools.find_listeners import _resolve_event_id
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


class FindDispatchersInput(ToolInput):
    """Identify the event whose dispatchers we want."""

    event: str = Field(
        description="Event FQN or ``event:<fqn>`` graph id.",
    )


class DispatcherRow(ToolOutput):
    """One method that fires the event."""

    class_fqn: str | None = None
    method: str
    file: str | None = None
    line: int | None = None


class FindDispatchersOutput(ToolOutput):
    """Container for a ``find_dispatchers`` response."""

    event: str | None = None
    total: int = 0
    returned: int = 0
    dispatchers: list[DispatcherRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("dispatchers",)


class FindDispatchersTool:
    """Find every method that fires a given event."""

    name: ClassVar[str] = "find_dispatchers"
    description: ClassVar[str] = (
        "Given an event FQN, return every method that fires it. "
        "**Argument:** ``event`` (string) — the event FQN, e.g. "
        '``event="App\\\\Events\\\\OrderPlaced"``. '
        "Each row points at a caller's class, method, file, and line so "
        "agents can jump directly to the source."
    )
    input_model: ClassVar[type[ToolInput]] = FindDispatchersInput
    output_model: ClassVar[type[ToolOutput]] = FindDispatchersOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: FindDispatchersInput,
        ctx: QueryContext,
    ) -> FindDispatchersOutput:
        """Walk ``FIRES`` edges backwards from the event."""
        graph = ctx.storage.graph().load()

        event_id = _resolve_event_id(graph, payload.event)
        if event_id is None:
            return FindDispatchersOutput(
                event=payload.event,
                error=f"No event found matching {payload.event!r}.",
                error_code="event_not_found",
            )

        rows: list[DispatcherRow] = []
        for edge in incoming(graph, event_id, EdgeKind.FIRES):
            method_node = graph.node_by_id(edge.source)
            if method_node is None:
                continue
            attrs = method_node.attributes
            rows.append(
                DispatcherRow(
                    class_fqn=str_attr(attrs, "class_fqn"),
                    method=method_node.name,
                    file=str_attr(attrs, "file"),
                    line=int_attr(attrs, "line"),
                ),
            )

        rows.sort(key=lambda r: (r.class_fqn or "", r.method))

        return FindDispatchersOutput(
            event=event_id,
            total=len(rows),
            returned=len(rows),
            dispatchers=rows,
        )
