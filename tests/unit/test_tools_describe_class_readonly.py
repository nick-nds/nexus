"""Unit tests for the ``readonly`` field on :class:`DescribeClassOutput`.

Pins audit finding P0-5. PHP 8.2+ added ``final readonly class Foo`` —
heavily used in DTOs. The modifier changes object semantics (every
property is implicitly readonly after construction), so dropping it
loses meaningful information. The reflection schema bumped to 2.2.0
to carry it; the field defaults to ``None`` on indexes built against
older schemas so consumers can tell "we don't know" apart from "we
know it isn't readonly".
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_class import (
    DescribeClassInput,
    DescribeClassTool,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_class(graph: Graph, fqn: str, *, readonly: object) -> None:
    """Add a class node. ``readonly`` may be True, False, or omitted (sentinel)."""
    attrs: dict[str, object] = {"fqn": fqn, "final": True, "abstract": False}
    # Sentinel ``...`` means "don't add the key" — simulates an old
    # schema-2.1.0 index that predates the field entirely.
    if readonly is not ...:
        attrs["readonly"] = readonly
    graph.add_node(
        Node(
            id=f"class:{fqn}",
            kind=NodeKind.CLASS,
            name=fqn.rsplit("\\", 1)[-1],
            attributes=attrs,
        ),
    )


def test_describe_class_surfaces_readonly_true() -> None:
    g = Graph()
    _add_class(g, "App\\DTOs\\Customer", readonly=True)
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\DTOs\\Customer"),
        ctx,
    )

    assert output.readonly is True


def test_describe_class_surfaces_readonly_false() -> None:
    g = Graph()
    _add_class(g, "App\\Models\\User", readonly=False)
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Models\\User"),
        ctx,
    )

    assert output.readonly is False


def test_describe_class_readonly_is_none_when_attr_absent_old_schema() -> None:
    """Indexes built with schema ≤ 2.1.0 don't carry the attribute.

    The agent reading ``readonly: null`` should treat that as "unknown",
    not as ``false`` — important because the difference matters when
    deciding whether the class is a DTO.
    """
    g = Graph()
    _add_class(g, "Legacy\\Old\\Class_", readonly=...)  # don't set the attr
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="Legacy\\Old\\Class_"),
        ctx,
    )

    assert output.readonly is None
