"""The graph builder threads queued/file onto listener nodes and
source/order onto ``LISTENS_TO`` edges.

``queued`` and ``file`` are properties of the listener class, so they
live on the node. ``source`` (how it was wired) and ``order`` (its
position in the event's listener list, i.e. execution order) are
properties of *this* wiring, so they live on the edge - a single
listener class can be wired to several events with different sources
and positions.
"""

from __future__ import annotations

from nexus.core.graph.builder import GraphBuilder
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import listener_id
from nexus.core.graph.types import EdgeKind
from nexus.core.reflection.document import EventListenerEntry, ListenerCallback

EVENT = "App\\Events\\Brand\\BrandCreated"
FIRST = "App\\Listeners\\Brand\\CreateCpanelSubdomain"
SECOND = "App\\Listeners\\Brand\\CreateStoreInChannels"


def _cb(fqn: str, *, queued: bool, source: str) -> ListenerCallback:
    short = fqn.rsplit("\\", 1)[-1]
    return ListenerCallback.model_validate(
        {
            "kind": "class",
            "class": fqn,
            "method": "handle",
            "file": f"app/Listeners/{short}.php",
            "queued": queued,
            "source": source,
        },
    )


def _entry() -> EventListenerEntry:
    return EventListenerEntry(
        event=EVENT,
        listeners=[
            _cb(FIRST, queued=True, source="listen"),
            _cb(SECOND, queued=False, source="discovered"),
        ],
    )


def test_listener_node_carries_queued_and_file() -> None:
    graph = Graph()

    GraphBuilder()._build_events(graph, [_entry()])

    node = graph.node_by_id(listener_id(FIRST, "handle"))
    assert node is not None
    assert node.attributes["queued"] is True
    assert node.attributes["file"] == "app/Listeners/CreateCpanelSubdomain.php"


def test_listens_to_edge_carries_source_and_execution_order() -> None:
    graph = Graph()

    GraphBuilder()._build_events(graph, [_entry()])

    by_source = {e.source: e for e in graph.edges if e.kind == EdgeKind.LISTENS_TO}
    first = by_source[listener_id(FIRST, "handle")]
    second = by_source[listener_id(SECOND, "handle")]

    assert first.attributes["source"] == "listen"
    assert first.attributes["order"] == 0
    assert second.attributes["source"] == "discovered"
    assert second.attributes["order"] == 1
