"""Unit tests for :class:`FindCallersTool`.

Covers the happy path (CALLS edges populated, callers returned in
deterministic order), method-not-found, and - pinning audit finding
P0-8 - the ``calls_not_indexed`` structured error when the index was
built without an LSP.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools.find_callers import FindCallersInput, FindCallersTool


def _make_ctx(graph: Graph, coverage: Coverage | None = None) -> QueryContext:
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle
    return QueryContext(
        storage=storage,
        budget=ResponseBudget(),
        coverage=coverage,
    )


def _add_method(g: Graph, class_fqn: str, method: str) -> str:
    method_id = f"method:{class_fqn}::{method}"
    g.add_node(
        Node(
            id=method_id,
            kind=NodeKind.METHOD,
            name=method,
            attributes={"class_fqn": class_fqn, "file": "/app.php", "line": 10},
        ),
    )
    return method_id


def _add_call(g: Graph, src: str, dst: str, *, line: int = 42) -> None:
    g.add_edge(
        Edge(
            source=src,
            target=dst,
            kind=EdgeKind.CALLS,
            attributes={"file": "/caller.php", "line": line},
        ),
    )


def test_returns_callers_in_deterministic_order_when_calls_indexed() -> None:
    g = Graph()
    target = _add_method(g, "App\\Models\\User", "scopeActive")
    caller_b = _add_method(g, "App\\Services\\B", "list")
    caller_a = _add_method(g, "App\\Services\\A", "find")
    _add_call(g, caller_b, target, line=11)
    _add_call(g, caller_a, target, line=22)
    ctx = _make_ctx(g, coverage=Coverage(calls_indexed=True))

    output = FindCallersTool().execute(
        FindCallersInput(method_fqn="App\\Models\\User::scopeActive"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 2
    # Sorted by (class_fqn, method, line) - A before B.
    assert [r.class_fqn for r in output.callers] == ["App\\Services\\A", "App\\Services\\B"]


def test_unknown_method_returns_method_not_found() -> None:
    g = Graph()
    ctx = _make_ctx(g)

    output = FindCallersTool().execute(
        FindCallersInput(method_fqn="App\\Models\\Nope::scope"),
        ctx,
    )

    assert output.error_code == "method_not_found"
    assert output.callers == []


def test_calls_not_indexed_returns_structured_error_when_method_exists() -> None:
    """Pinning P0-8 from the acme-platform audit.

    Without this guard, ``find_callers`` returns ``total: 0, error:
    null`` - indistinguishable from "this method has no callers". The
    agent has no way to know the question was unanswerable rather than
    answered "none".
    """
    g = Graph()
    target = _add_method(g, "App\\Models\\User", "scopeActive")
    _ = target  # unused - the method exists, but CALLS edges don't
    ctx = _make_ctx(g, coverage=Coverage(calls_indexed=False))

    output = FindCallersTool().execute(
        FindCallersInput(method_fqn="App\\Models\\User::scopeActive"),
        ctx,
    )

    assert output.error_code == "calls_not_indexed"
    assert output.callers == []
    assert "lsp" in (output.error or "").lower()
    # Method exists - we echo back the resolved id, not the raw input.
    assert output.method_fqn == "method:App\\Models\\User::scopeActive"


def test_calls_not_indexed_does_not_fire_when_method_not_found() -> None:
    """If the method doesn't exist, method_not_found wins over calls_not_indexed."""
    g = Graph()  # empty
    ctx = _make_ctx(g, coverage=Coverage(calls_indexed=False))

    output = FindCallersTool().execute(
        FindCallersInput(method_fqn="App\\Nope::x"),
        ctx,
    )

    assert output.error_code == "method_not_found"


def test_coverage_none_skips_guard() -> None:
    """Library callers / older tests build ctx without coverage; that path must work."""
    g = Graph()
    target = _add_method(g, "App\\Models\\User", "scopeActive")
    caller = _add_method(g, "App\\Services\\A", "find")
    _add_call(g, caller, target)
    ctx = _make_ctx(g, coverage=None)
    assert ctx.coverage is None

    output = FindCallersTool().execute(
        FindCallersInput(method_fqn="App\\Models\\User::scopeActive"),
        ctx,
    )

    assert output.error_code is None
    assert output.total == 1
