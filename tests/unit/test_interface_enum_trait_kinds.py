"""Tests for the ``interface``, ``enum``, ``trait`` NodeKinds (audit P0-1, P0-2).

PHP language constructs that aren't plain classes now get their own
first-class kinds. Before this change they were all coerced to
``CLASS`` with ``kinds: ["abstract"]``, which made it impossible to
ask "is X an interface?" or "list all interfaces in this module".
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.builder import _KIND_PRIORITY
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.describe_class import DescribeClassInput, DescribeClassTool
from nexus.core.query.tools.list_by_kind import ListByKindInput, ListByKindTool


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


# ---------------------------------------------------------------------------
# Enum / interface / trait are valid NodeKind values
# ---------------------------------------------------------------------------


def test_interface_is_a_valid_nodekind() -> None:
    assert NodeKind.INTERFACE.value == "interface"


def test_enum_is_a_valid_nodekind() -> None:
    assert NodeKind.ENUM.value == "enum"


def test_trait_is_a_valid_nodekind() -> None:
    assert NodeKind.TRAIT.value == "trait"


# ---------------------------------------------------------------------------
# Kind priority cascade
# ---------------------------------------------------------------------------


def test_php_language_kinds_outrank_laravel_role_kinds() -> None:
    """If a class somehow tags both ``interface`` and ``model``, the
    language-construct kind wins. Comes up rarely but the design has
    to be principled.
    """
    labels = [label for label, _ in _KIND_PRIORITY]
    # ``class`` isn't in the priority list — it's the implicit fallback
    # when no entry matches. So we compare against Laravel role kinds
    # that ARE in the list.
    for lang in ("interface", "enum", "trait"):
        for laravel in ("model", "service_provider", "controller"):
            assert labels.index(lang) < labels.index(laravel)


# ---------------------------------------------------------------------------
# describe_class surfaces enum cases
# ---------------------------------------------------------------------------


def test_describe_class_surfaces_enum_cases() -> None:
    g = Graph()
    g.add_node(
        Node(
            id="class:App\\Enums\\CustomerStatus",
            kind=NodeKind.ENUM,
            name="CustomerStatus",
            attributes={
                "fqn": "App\\Enums\\CustomerStatus",
                "final": True,
                "abstract": False,
                "cases": [
                    {"name": "Active", "value": "active"},
                    {"name": "Inactive", "value": "inactive"},
                    {"name": "Churned", "value": "churned"},
                ],
            },
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Enums\\CustomerStatus"),
        ctx,
    )

    assert output.kind == "enum"
    assert len(output.cases) == 3
    assert output.cases[0].name == "Active"
    assert output.cases[0].value == "active"


def test_describe_class_cases_empty_for_non_enum() -> None:
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
            },
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Models\\User"),
        ctx,
    )

    assert output.cases == []


def test_describe_class_unit_enum_cases_have_no_value() -> None:
    """Unit enums (``enum Color`` without backing type) → ``value=None``."""
    g = Graph()
    g.add_node(
        Node(
            id="class:App\\Enums\\Color",
            kind=NodeKind.ENUM,
            name="Color",
            attributes={
                "fqn": "App\\Enums\\Color",
                "final": True,
                "abstract": False,
                "cases": [
                    {"name": "Red", "value": None},
                    {"name": "Green", "value": None},
                    {"name": "Blue", "value": None},
                ],
            },
        ),
    )
    ctx = _make_ctx(g)

    output = DescribeClassTool().execute(
        DescribeClassInput(fqn="App\\Enums\\Color"),
        ctx,
    )

    assert output.kind == "enum"
    assert [c.name for c in output.cases] == ["Red", "Green", "Blue"]
    assert all(c.value is None for c in output.cases)


# ---------------------------------------------------------------------------
# list_by_kind accepts new kinds
# ---------------------------------------------------------------------------


def _add_class(g: Graph, fqn: str, kind: NodeKind) -> None:
    g.add_node(
        Node(
            id=f"class:{fqn}",
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn, "namespace": "\\".join(fqn.split("\\")[:-1])},
        ),
    )


def test_list_by_kind_accepts_interface() -> None:
    g = Graph()
    _add_class(g, "App\\Contracts\\Transformer", NodeKind.INTERFACE)
    _add_class(g, "App\\Contracts\\Repository", NodeKind.INTERFACE)
    ctx = _make_ctx(g)

    output = ListByKindTool().execute(ListByKindInput(kind="interface"), ctx)

    assert output.error_code is None
    assert output.total == 2
    assert {r.short_name for r in output.items} == {"Transformer", "Repository"}


def test_list_by_kind_accepts_enum() -> None:
    g = Graph()
    _add_class(g, "App\\Enums\\CustomerStatus", NodeKind.ENUM)
    _add_class(g, "App\\Enums\\OrderState", NodeKind.ENUM)
    ctx = _make_ctx(g)

    output = ListByKindTool().execute(ListByKindInput(kind="enum"), ctx)

    assert output.error_code is None
    assert output.total == 2


def test_list_by_kind_accepts_trait() -> None:
    g = Graph()
    _add_class(g, "App\\Concerns\\HasTimestamps", NodeKind.TRAIT)
    ctx = _make_ctx(g)

    output = ListByKindTool().execute(ListByKindInput(kind="trait"), ctx)

    assert output.error_code is None
    assert output.total == 1
