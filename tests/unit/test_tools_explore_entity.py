"""Unit tests for :class:`ExploreEntityTool`.

Builds a small in-memory graph with classes spread across a few
kinds and asserts the matcher returns the right groups in the right
precedence order. The classifier-side rule has its own corpus
coverage in ``test_classifier_corpus.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.explore_entity import (
    ExploreEntityInput,
    ExploreEntityTool,
)


def _add_class_node(graph: Graph, fqn: str, kind: NodeKind) -> None:
    graph.add_node(
        Node(
            id=f"class:{fqn}",
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={
                "fqn": fqn,
                "namespace": fqn.rsplit("\\", 1)[0] if "\\" in fqn else None,
                "file": f"/app/{fqn.replace(chr(92), '/')}.php",
            },
        ),
    )


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _make_graph() -> Graph:
    """Mini graph mimicking a CQRS-shaped CRM (the user's project)."""
    g = Graph()
    # Models
    _add_class_node(g, "App\\Models\\User", NodeKind.MODEL)
    _add_class_node(g, "App\\Modules\\CRM\\Domain\\Models\\Lead", NodeKind.MODEL)
    _add_class_node(g, "App\\Modules\\Operations\\Domain\\Models\\Product", NodeKind.MODEL)
    # Commands & handlers
    _add_class_node(g, "App\\Modules\\Operations\\Commands\\CreateProductCommand", NodeKind.CLASS)
    _add_class_node(
        g, "App\\Modules\\Operations\\Commands\\CreateProductCommandHandler", NodeKind.CLASS
    )
    _add_class_node(g, "App\\Modules\\Operations\\Commands\\ActivateProductCommand", NodeKind.CLASS)
    # Events
    _add_class_node(g, "App\\Modules\\Operations\\Events\\ProductCreated", NodeKind.EVENT)
    _add_class_node(g, "App\\Modules\\Operations\\Events\\ProductActivated", NodeKind.EVENT)
    # An unrelated class so the matcher must filter
    _add_class_node(g, "App\\Modules\\CRM\\Services\\OrderService", NodeKind.CLASS)
    return g


# ---------------------------------------------------------------------------
# Match precedence
# ---------------------------------------------------------------------------


def test_exact_short_name_match_wins() -> None:
    """Lead resolves to the model first, before any substring match."""
    ctx = _make_ctx(_make_graph())
    output = ExploreEntityTool().execute(ExploreEntityInput(name="Lead"), ctx)

    assert output.error_code is None
    assert output.total >= 1

    # First group should contain an ``exact_name`` match.
    first_group = output.groups[0]
    qualities = {m.match_quality for m in first_group.matches}
    assert "exact_name" in qualities


def test_substring_match_groups_by_kind() -> None:
    """``Product`` matches across model, commands, and events."""
    ctx = _make_ctx(_make_graph())
    output = ExploreEntityTool().execute(ExploreEntityInput(name="Product"), ctx)

    assert output.error_code is None
    kinds = {g.kind for g in output.groups}
    # Models, classes (commands sit under generic class), and events
    assert "model" in kinds
    assert "class" in kinds
    assert "event" in kinds


def test_no_matches_returns_structured_error() -> None:
    """No match yields a refusable error code, not an empty success."""
    ctx = _make_ctx(_make_graph())
    output = ExploreEntityTool().execute(ExploreEntityInput(name="ZzNonExistent"), ctx)

    assert output.error_code == "no_matches"
    assert output.total == 0
    assert output.groups == []


def test_max_per_kind_caps_results_but_reports_total() -> None:
    g = Graph()
    for i in range(20):
        _add_class_node(g, f"App\\Items\\Item{i:02d}Service", NodeKind.CLASS)
    ctx = _make_ctx(g)

    output = ExploreEntityTool().execute(
        ExploreEntityInput(name="Service", max_per_kind=5),
        ctx,
    )

    assert output.error_code is None
    cls_group = next(g for g in output.groups if g.kind == "class")
    assert cls_group.total == 20
    assert cls_group.returned == 5
    assert len(cls_group.matches) == 5


def test_exact_fqn_match_short_circuits() -> None:
    ctx = _make_ctx(_make_graph())
    output = ExploreEntityTool().execute(
        ExploreEntityInput(name="App\\Models\\User"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 1
    [match] = output.groups[0].matches
    assert match.fqn == "App\\Models\\User"
    assert match.match_quality == "exact_fqn"


def test_groups_with_exact_matches_sort_first() -> None:
    """A kind containing an exact match outranks a kind with only substring matches."""
    ctx = _make_ctx(_make_graph())

    output = ExploreEntityTool().execute(ExploreEntityInput(name="User"), ctx)

    assert output.error_code is None
    # ``User`` is the exact short name of the model class; the model
    # group must lead even if other kinds have substring hits later.
    assert output.groups[0].kind == "model"


# ---------------------------------------------------------------------------
# Coverage attached by the engine
# ---------------------------------------------------------------------------


def test_output_carries_coverage_when_engine_attaches_one() -> None:
    """The tool itself doesn't set ``coverage`` - that's the engine's job."""
    ctx = _make_ctx(_make_graph())
    output = ExploreEntityTool().execute(ExploreEntityInput(name="User"), ctx)

    # Tool returns ``None`` for coverage; the engine substitutes the
    # context's coverage block in production. Verifying ``None`` here
    # guards against a tool accidentally setting it itself.
    assert output.coverage is None
