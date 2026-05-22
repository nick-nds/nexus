"""``find_event_chains`` — multi-hop event fan-out traversal.

Starting from an event, walk listeners → any events they fire →
their listeners, up to a depth cap. Returns a flat list of
(depth, event, listener, next_event) rows — simpler for agents
to consume than a nested tree, and easy to re-assemble if a
caller wants the tree shape.

This is the tool for "what is the full chain of side-effects a
``UserRegistered`` event kicks off?". It deliberately does not
walk jobs or other side effects; use :mod:`get_request_flow`
for the per-route view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr
from nexus.core.query.tools.find_listeners import _resolve_event_id
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.query.context import QueryContext


# A single chain walk should stay small. This cap protects against
# cyclic graphs (A listens to B, B listens to A) and very deep
# trees that would overwhelm the response budget.
DEFAULT_CHAIN_DEPTH = 3
MAX_CHAIN_DEPTH = 6


class FindEventChainsInput(ToolInput):
    """Identify the starting event."""

    event: str = Field(
        description="Event FQN or ``event:<fqn>`` graph id.",
    )
    max_depth: int = Field(
        default=DEFAULT_CHAIN_DEPTH,
        ge=1,
        le=MAX_CHAIN_DEPTH,
        description=(
            "How many hops to walk (1 = immediate listeners only). "
            "Capped at 6 to keep responses bounded."
        ),
    )


class ChainStep(ToolOutput):
    """One (parent_event, listener, child_event) hop in a chain."""

    depth: int
    parent_event: str
    listener: str
    listener_fqn: str | None = None
    child_event: str | None = None


class FindEventChainsOutput(ToolOutput):
    """Flat list of chain steps discovered by the BFS."""

    event: str | None = None
    depth_reached: int = 0
    steps: list[ChainStep] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("steps",)


class FindEventChainsTool:
    """Walk the event → listener → fired-event chain for N hops."""

    name: ClassVar[str] = "find_event_chains"
    description: ClassVar[str] = (
        "Starting from one event, walk its listeners, then any events "
        "those listeners fire, up to ``max_depth`` hops. "
        "**Argument:** ``event`` (string) — the starting event FQN, "
        'e.g. ``event="App\\\\Events\\\\OrderPlaced"``. '
        "**Optional:** ``max_depth`` (int, default 3, max 6) — hop budget. "
        "Returns a flat list of (depth, parent_event, listener, "
        "child_event) rows — rebuild the tree client-side if needed."
    )
    input_model: ClassVar[type[ToolInput]] = FindEventChainsInput
    output_model: ClassVar[type[ToolOutput]] = FindEventChainsOutput
    latency_budget_ms: ClassVar[int] = 300

    def execute(
        self,
        payload: FindEventChainsInput,
        ctx: QueryContext,
    ) -> FindEventChainsOutput:
        """Breadth-first walk from the starting event."""
        graph = ctx.storage.graph().load()

        start_id = _resolve_event_id(graph, payload.event)
        if start_id is None:
            return FindEventChainsOutput(
                event=payload.event,
                error=f"No event found matching {payload.event!r}.",
                error_code="event_not_found",
            )

        steps: list[ChainStep] = []
        visited_events: set[str] = {start_id}
        frontier: list[str] = [start_id]
        depth_reached = 0

        for depth in range(1, payload.max_depth + 1):
            next_frontier: list[str] = []
            for event_id in frontier:
                event_node = graph.node_by_id(event_id)
                parent_name = event_node.name if event_node is not None else event_id
                for listener_step in _walk_listeners(graph, event_id):
                    steps.append(
                        ChainStep(
                            depth=depth,
                            parent_event=parent_name,
                            listener=listener_step.listener,
                            listener_fqn=listener_step.listener_fqn,
                            child_event=listener_step.child_event_name,
                        ),
                    )
                    if listener_step.child_event_id is None:
                        continue
                    if listener_step.child_event_id in visited_events:
                        continue
                    visited_events.add(listener_step.child_event_id)
                    next_frontier.append(listener_step.child_event_id)

            if not next_frontier:
                depth_reached = depth
                break
            depth_reached = depth
            frontier = next_frontier

        steps.sort(key=lambda s: (s.depth, s.parent_event, s.listener))

        return FindEventChainsOutput(
            event=start_id,
            depth_reached=depth_reached,
            steps=steps,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ListenerStep:
    __slots__ = ("child_event_id", "child_event_name", "listener", "listener_fqn")

    def __init__(
        self,
        *,
        listener: str,
        listener_fqn: str | None,
        child_event_id: str | None,
        child_event_name: str | None,
    ) -> None:
        self.listener = listener
        self.listener_fqn = listener_fqn
        self.child_event_id = child_event_id
        self.child_event_name = child_event_name


def _walk_listeners(graph: Graph, event_id: str) -> list[_ListenerStep]:
    """For one event, enumerate (listener, next_event?) tuples."""
    steps: list[_ListenerStep] = []
    for edge in incoming(graph, event_id, EdgeKind.LISTENS_TO):
        listener_node = graph.node_by_id(edge.source)
        if listener_node is None:
            continue
        fqn = str_attr(listener_node.attributes, "class_fqn") or str_attr(
            listener_node.attributes,
            "fqn",
        )

        # A listener may fire zero, one, or many downstream events.
        fired = outgoing(graph, listener_node.id, EdgeKind.FIRES)
        if not fired:
            steps.append(
                _ListenerStep(
                    listener=listener_node.name,
                    listener_fqn=fqn,
                    child_event_id=None,
                    child_event_name=None,
                ),
            )
            continue

        for fire_edge in fired:
            child_node = graph.node_by_id(fire_edge.target)
            steps.append(
                _ListenerStep(
                    listener=listener_node.name,
                    listener_fqn=fqn,
                    child_event_id=fire_edge.target,
                    child_event_name=child_node.name if child_node else fire_edge.target,
                ),
            )
    return steps
