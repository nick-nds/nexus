"""Tests for the interfaces_declared / interfaces_inherited split (audit P0-4).

Before this change, ``describe_class.interfaces`` was the union of
declared + inherited interfaces. An Eloquent model that extended
``Model`` reported 9 inherited contracts (ArrayAccess, Arrayable, …)
even though the class itself declared zero. Agents asking "what does
this class promise?" got noise.

Schema 2.4.0 splits the two: ``interfaces`` means declared only,
``interfaces_inherited`` carries the transitive set.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_class import DescribeClassInput, DescribeClassTool


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def test_interfaces_inherited_surfaces_in_describe_class() -> None:
    g = Graph()
    g.add_node(
        Node(
            id="class:App\\Models\\User",
            kind=NodeKind.MODEL,
            name="User",
            attributes={
                "fqn": "App\\Models\\User",
                "final": True,
                "abstract": False,
                "interfaces_inherited": [
                    "ArrayAccess",
                    "Illuminate\\Contracts\\Support\\Arrayable",
                    "Illuminate\\Contracts\\Support\\Jsonable",
                ],
            },
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Models\\User"),
        ctx,
    )

    # Inherited list is populated; declared (``interfaces``) is empty
    # because no IMPLEMENTS edges in this synthetic graph.
    assert output.interfaces == []
    assert "ArrayAccess" in output.interfaces_inherited
    assert "Illuminate\\Contracts\\Support\\Arrayable" in output.interfaces_inherited


def test_interfaces_inherited_empty_when_no_attr() -> None:
    """Old indexes (schema ≤ 2.3.0) don't carry the field - empty list, no error."""
    g = Graph()
    g.add_node(
        Node(
            id="class:App\\Plain\\Cls",
            kind=NodeKind.CLASS,
            name="Cls",
            attributes={"fqn": "App\\Plain\\Cls", "abstract": False, "final": False},
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Plain\\Cls"),
        ctx,
    )

    assert output.interfaces_inherited == []
