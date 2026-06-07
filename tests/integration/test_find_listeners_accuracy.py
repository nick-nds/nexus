"""Accuracy regressions for ``find_listeners``.

A field report against a Laravel 11 app surfaced four defects:

* ``queued`` was always ``false`` even for listeners implementing
  ``ShouldQueue`` (the flag was never populated);
* ``file`` was always ``null`` for class listeners;
* there was no way to tell *how* a listener was wired (explicit
  ``$listen`` map vs. discovered);
* listeners were re-sorted alphabetically, destroying the dispatcher's
  execution order.

These tests build a graph with the attributes the builder now emits and
assert the tool surfaces them faithfully.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.adapters.storage import ProjectStorage
from nexus.core.graph.graph import Graph
from nexus.core.graph.ids import event_id, listener_id
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.find_listeners import FindListenersInput, FindListenersTool

pytestmark = pytest.mark.integration

EVENT = "App\\Events\\Brand\\BrandCreated"
# Registered first in $listen, but sorts last alphabetically - so an
# alphabetical sort would wrongly move it to the end.
QUEUED = "App\\Listeners\\Brand\\ZetaCreateCpanelSubdomain"
# Auto-discovered, sorts first alphabetically.
DISCOVERED = "App\\Listeners\\Brand\\AlphaCreateStoreInChannels"


def _graph() -> Graph:
    graph = Graph()
    graph.add_node(
        Node(id=event_id(EVENT), kind=NodeKind.EVENT, name="BrandCreated", attributes={}),
    )
    listeners = [
        (QUEUED, True, "app/Listeners/Brand/ZetaCreateCpanelSubdomain.php", "listen"),
        (DISCOVERED, False, "app/Listeners/Brand/AlphaCreateStoreInChannels.php", "discovered"),
    ]
    for order, (fqn, queued, file, source) in enumerate(listeners):
        lid = listener_id(fqn, "handle")
        graph.add_node(
            Node(
                id=lid,
                kind=NodeKind.LISTENER,
                name=fqn,
                attributes={
                    "class_fqn": fqn,
                    "method": "handle",
                    "queued": queued,
                    "file": file,
                },
            ),
        )
        graph.add_edge(
            Edge(
                source=lid,
                target=event_id(EVENT),
                kind=EdgeKind.LISTENS_TO,
                attributes={"order": order, "source": source},
            ),
        )
    return graph


def _run(tmp_path: Path):
    storage = ProjectStorage(root=tmp_path / ".nexus", slug="lst")
    assert storage.graph().persist(_graph()).ok
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    return FindListenersTool().execute(FindListenersInput(event=EVENT), ctx)


def test_reports_queued_true_for_should_queue_listener(tmp_path: Path) -> None:
    out = _run(tmp_path)

    by_fqn = {r.listener_fqn: r for r in out.listeners}
    assert by_fqn[QUEUED].queued is True
    assert by_fqn[DISCOVERED].queued is False


def test_populates_source_file(tmp_path: Path) -> None:
    out = _run(tmp_path)

    by_fqn = {r.listener_fqn: r for r in out.listeners}
    assert by_fqn[QUEUED].file == "app/Listeners/Brand/ZetaCreateCpanelSubdomain.php"


def test_reports_registration_source(tmp_path: Path) -> None:
    out = _run(tmp_path)

    by_fqn = {r.listener_fqn: r for r in out.listeners}
    assert by_fqn[QUEUED].source == "listen"
    assert by_fqn[DISCOVERED].source == "discovered"


def test_returns_listeners_in_execution_order_not_alphabetical(tmp_path: Path) -> None:
    out = _run(tmp_path)

    # Execution order is QUEUED (order 0) then DISCOVERED (order 1);
    # an alphabetical sort would put DISCOVERED ("Alpha...") first.
    assert [r.listener_fqn for r in out.listeners] == [QUEUED, DISCOVERED]
