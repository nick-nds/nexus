r"""``find_listeners`` — list the listeners subscribed to an event.

Given an event FQN (e.g. ``App\Events\UserRegistered``), walk
the ``LISTENS_TO`` edges backwards to enumerate every listener
the project has wired up to it. Returns listener metadata —
class FQN, whether it's queued, and the handler method name —
so agents can decide where to start reading.

The event is identified by its stable graph id
(``event:<fqn>``) or by its FQN. Wildcard listeners are picked
up via the event node's ``wildcard`` flag but treated as
ordinary listeners in the output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import bool_attr, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.query.context import QueryContext


class FindListenersInput(ToolInput):
    """Identify the event whose listeners we want."""

    event: str = Field(
        description=(
            "Event FQN (``App\\Events\\UserRegistered``) or graph id "
            "(``event:App\\Events\\UserRegistered``)."
        ),
    )


class ListenerRow(ToolOutput):
    """One listener in the response."""

    listener_fqn: str
    short_name: str
    method: str | None = None
    queued: bool = False
    file: str | None = None


class FindListenersOutput(ToolOutput):
    """Container for a ``find_listeners`` response."""

    event: str | None = None
    total: int = 0
    returned: int = 0
    listeners: list[ListenerRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("listeners",)


class FindListenersTool:
    """List every listener subscribed to an event."""

    name: ClassVar[str] = "find_listeners"
    description: ClassVar[str] = (
        "Given an event FQN, return every listener wired up to it via "
        "Laravel's event dispatcher. Each row includes the listener's "
        "class FQN, the handler method, whether it implements "
        "``ShouldQueue``, and the source file."
    )
    input_model: ClassVar[type[ToolInput]] = FindListenersInput
    output_model: ClassVar[type[ToolOutput]] = FindListenersOutput
    latency_budget_ms: ClassVar[int] = 200

    def execute(
        self,
        payload: FindListenersInput,
        ctx: QueryContext,
    ) -> FindListenersOutput:
        """Walk ``LISTENS_TO`` edges backwards from the event."""
        graph = ctx.storage.graph().load()

        event_id = _resolve_event_id(graph, payload.event)
        if event_id is None:
            return FindListenersOutput(
                event=payload.event,
                error=f"No event found matching {payload.event!r}.",
                error_code="event_not_found",
            )

        rows: list[ListenerRow] = []
        for edge in incoming(graph, event_id, EdgeKind.LISTENS_TO):
            listener_node = graph.node_by_id(edge.source)
            if listener_node is None:
                continue
            attrs = listener_node.attributes
            rows.append(
                ListenerRow(
                    listener_fqn=str_attr(attrs, "class_fqn")
                    or str_attr(attrs, "fqn")
                    or listener_node.name,
                    short_name=listener_node.name,
                    method=str_attr(attrs, "method"),
                    queued=bool_attr(attrs, "queued"),
                    file=str_attr(attrs, "file"),
                ),
            )

        rows.sort(key=lambda r: r.listener_fqn)

        return FindListenersOutput(
            event=event_id,
            total=len(rows),
            returned=len(rows),
            listeners=rows,
        )


def _resolve_event_id(graph: Graph, query: str) -> str | None:
    """Normalise the caller's event argument to the id LISTENS_TO edges target.

    The graph stores events two ways depending on which extractor pass
    surfaced them:

    * Events found via the reflection's ``events`` section are added
      with id ``event:<fqn>``.
    * Events found via the class walker (any class with the right
      naming convention) are added with id ``class:<fqn>`` and
      ``kind=event``.

    Critically, ``LISTENS_TO`` edges always target ``event:<fqn>``
    regardless of which form the event-node was stored as. So we
    return the ``event:<fqn>`` form unconditionally — even when only
    the ``class:`` node exists — so the traversal that follows finds
    the edges.
    """
    fqn = query
    for prefix in ("event:", "class:"):
        if fqn.startswith(prefix):
            fqn = fqn[len(prefix) :]
            break

    canonical = f"event:{fqn}"
    if graph.node_by_id(canonical) is not None:
        return canonical

    class_form = f"class:{fqn}"
    class_node = graph.node_by_id(class_form)
    if class_node is not None and class_node.kind == NodeKind.EVENT:
        return canonical

    # Last resort: short-name match. Recover the FQN from whichever id
    # form the node uses, then return the canonical edge-target form.
    for node in graph.nodes:
        if node.kind == NodeKind.EVENT and node.name == query:
            for prefix in ("event:", "class:"):
                if node.id.startswith(prefix):
                    return f"event:{node.id[len(prefix) :]}"
            return node.id
    return None
