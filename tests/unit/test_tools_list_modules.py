"""Unit tests for :class:`ListModulesTool` and prefix detection."""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.tools.list_modules import (
    ListModulesInput,
    ListModulesTool,
    detect_module_prefix,
)


def _make_ctx(graph: Graph) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(storage=storage, budget=ResponseBudget())


def _add_class(g: Graph, fqn: str, kind: NodeKind) -> None:
    g.add_node(
        Node(
            id=f"class:{fqn}",
            kind=kind,
            name=fqn.rsplit("\\", 1)[-1],
            attributes={"fqn": fqn},
        ),
    )


# ---------------------------------------------------------------------------
# Prefix detection helper
# ---------------------------------------------------------------------------


def test_ddd_module_prefix_uses_modules_segment() -> None:
    """``App\\Modules\\CRM\\Customers\\X`` resolves to ``App\\Modules\\CRM``."""
    assert detect_module_prefix("App\\Modules\\CRM\\Customers\\Customer") == "App\\Modules\\CRM"


def test_vendor_namespaced_ddd_module_prefix() -> None:
    """Module detection works regardless of vendor depth."""
    assert (
        detect_module_prefix("Synthesq\\Relay\\Modules\\CRM\\Leads\\Lead")
        == "Synthesq\\Relay\\Modules\\CRM"
    )


def test_standard_laravel_falls_back_to_first_two_segments() -> None:
    """No ``Modules`` segment → first two segments win."""
    assert detect_module_prefix("App\\Models\\User") == "App\\Models"
    assert detect_module_prefix("App\\Http\\Controllers\\X") == "App\\Http"


def test_top_level_class_returns_none() -> None:
    assert detect_module_prefix("RootClass") is None


# ---------------------------------------------------------------------------
# ListModulesTool
# ---------------------------------------------------------------------------


def _make_mixed_graph() -> Graph:
    """A graph with both DDD and standard-Laravel namespaces."""
    g = Graph()
    # DDD modules
    _add_class(g, "App\\Modules\\CRM\\Customers\\Customer", NodeKind.MODEL)
    _add_class(g, "App\\Modules\\CRM\\Leads\\Lead", NodeKind.MODEL)
    _add_class(g, "App\\Modules\\CRM\\Events\\LeadCreated", NodeKind.EVENT)
    _add_class(g, "App\\Modules\\Operations\\Products\\Product", NodeKind.MODEL)
    _add_class(g, "App\\Modules\\Operations\\Jobs\\ReorderJob", NodeKind.JOB)
    # Standard Laravel
    _add_class(g, "App\\Http\\Controllers\\AuthController", NodeKind.CONTROLLER)
    _add_class(g, "App\\Http\\Controllers\\HomeController", NodeKind.CONTROLLER)
    _add_class(g, "App\\Models\\User", NodeKind.MODEL)
    return g


def test_list_modules_groups_ddd_and_standard_namespaces() -> None:
    ctx = _make_ctx(_make_mixed_graph())
    output = ListModulesTool().execute(ListModulesInput(min_classes=1), ctx)

    prefixes = {m.prefix for m in output.modules}
    # DDD modules
    assert "App\\Modules\\CRM" in prefixes
    assert "App\\Modules\\Operations" in prefixes
    # Standard Laravel
    assert "App\\Http" in prefixes
    assert "App\\Models" in prefixes


def test_list_modules_class_count_per_prefix() -> None:
    ctx = _make_ctx(_make_mixed_graph())
    output = ListModulesTool().execute(ListModulesInput(min_classes=1), ctx)

    by_prefix = {m.prefix: m for m in output.modules}
    # CRM has 3 classes (Customer, Lead, LeadCreated event)
    assert by_prefix["App\\Modules\\CRM"].class_count == 3
    # Http has 2 controllers
    assert by_prefix["App\\Http"].class_count == 2


def test_list_modules_sorted_by_class_count_desc() -> None:
    ctx = _make_ctx(_make_mixed_graph())
    output = ListModulesTool().execute(ListModulesInput(min_classes=1), ctx)

    counts = [m.class_count for m in output.modules]
    assert counts == sorted(counts, reverse=True)


def test_min_classes_filters_small_modules() -> None:
    """A module with only 1 class is hidden when ``min_classes=2``."""
    ctx = _make_ctx(_make_mixed_graph())
    output = ListModulesTool().execute(ListModulesInput(min_classes=3), ctx)

    # Only CRM has 3+ classes in the mixed graph.
    prefixes = {m.prefix for m in output.modules}
    assert "App\\Modules\\CRM" in prefixes
    # Operations has 2 → filtered out.
    assert "App\\Modules\\Operations" not in prefixes


def test_module_kinds_breakdown() -> None:
    ctx = _make_ctx(_make_mixed_graph())
    output = ListModulesTool().execute(ListModulesInput(min_classes=1), ctx)

    crm = next(m for m in output.modules if m.prefix == "App\\Modules\\CRM")
    assert crm.kinds == {"model": 2, "event": 1}
