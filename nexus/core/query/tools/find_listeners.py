r"""``find_listeners`` - list the listeners subscribed to an event.

Given an event FQN (e.g. ``App\Events\UserRegistered``), walk
the ``LISTENS_TO`` edges backwards to enumerate every listener
the project has wired up to it. Returns listener metadata -
class FQN, the handler method, whether it's queued, the source
file, and how it was wired (``source``) - in execution order, so
agents can decide where to start reading.

The event is identified by its stable graph id
(``event:<fqn>``) or by its FQN. Wildcard listeners are picked
up via the event node's ``wildcard`` flag but treated as
ordinary listeners in the output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.ids import class_id
from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import bool_attr, int_attr, str_attr
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
    #: How the listener was wired: ``"listen"`` (explicit
    #: ``EventServiceProvider::$listen`` map) or ``"discovered"`` (Laravel
    #: auto-discovery, ``Event::listen``, or a subscriber). ``None`` when
    #: the index predates source tracking.
    source: str | None = None


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
        "Laravel's event dispatcher. "
        "**Argument:** ``event`` (string) - the event FQN, e.g. "
        '``event="App\\\\Events\\\\OrderPlaced"``. '
        "Each row includes the listener's class FQN, the handler method, "
        "whether it implements ``ShouldQueue`` (``queued``), the source "
        "file, and how it was wired (``source``: ``listen`` for an explicit "
        "``$listen`` entry, ``discovered`` otherwise). Listeners are "
        "returned in execution order."
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

        ordered_rows: list[tuple[int, ListenerRow]] = []
        for edge in incoming(graph, event_id, EdgeKind.LISTENS_TO):
            listener_node = graph.node_by_id(edge.source)
            if listener_node is None:
                continue
            attrs = listener_node.attributes
            # ``order`` and ``source`` are wiring-level, so they live on
            # the edge, not the listener node.
            order = int_attr(edge.attributes, "order")
            ordered_rows.append(
                (
                    order if order is not None else len(ordered_rows),
                    ListenerRow(
                        listener_fqn=str_attr(attrs, "class_fqn")
                        or str_attr(attrs, "fqn")
                        or listener_node.name,
                        short_name=listener_node.name,
                        method=str_attr(attrs, "method"),
                        queued=bool_attr(attrs, "queued"),
                        file=str_attr(attrs, "file"),
                        source=str_attr(edge.attributes, "source"),
                    ),
                ),
            )

        # Preserve the dispatcher's registration order (= execution order
        # in modern Laravel). Break ties on the FQN so the output stays
        # deterministic when ``order`` is absent on a legacy index.
        ordered_rows.sort(key=lambda pair: (pair[0], pair[1].listener_fqn))
        rows = [row for _, row in ordered_rows]

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
    return the ``event:<fqn>`` form unconditionally - even when only
    the ``class:`` node exists - so the traversal that follows finds
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


def _event_edge_target_ids(graph: Graph, query: str) -> list[str]:
    r"""Every node id an event's edges may target.

    Events are stored two ways (see :func:`_resolve_event_id`), and
    different passes attach edges to different forms: the events
    section targets ``event:<fqn>`` (``LISTENS_TO``), while the
    static-analysis dispatch pass targets ``class:<fqn>`` (``FIRES``,
    via :func:`~nexus.core.graph.builder_findings.apply_static_findings`).
    Reverse-traversal tools must check both forms or they silently
    miss edges - the bug that made ``find_dispatchers`` return zero
    for every dispatched event.

    Returns both forms (canonical ``event:<fqn>`` first, then the
    ``class:<fqn>`` form) or an empty list when the event can't be
    resolved at all.
    """
    canonical = _resolve_event_id(graph, query)
    if canonical is None:
        return []
    return [canonical, class_id(canonical[len("event:") :])]
