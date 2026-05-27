"""Unit tests for the query-trace writer (Gap #1).

The trace module powers ``nexus ask --trace`` and the MCP server's
``NEXUS_TRACE_DIR`` env-var path. These tests cover the writer, the
factory, and the engine wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Node, NodeKind
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.classifier import QueryClassifier
from nexus.core.query.context import QueryContext
from nexus.core.query.engine import QueryEngine
from nexus.core.query.registry import ToolRegistry
from nexus.core.query.tools import register_builtin_tools
from nexus.core.query.trace import (
    JsonlQueryTrace,
    NullQueryTrace,
    default_trace_path,
    open_trace,
    record_classifier_decision,
    record_tool_executed,
    trace_path_from_env,
)

if TYPE_CHECKING:
    import pytest


# ---------------------------------------------------------------------------
# JsonlQueryTrace
# ---------------------------------------------------------------------------


class TestJsonlQueryTrace:
    def test_record_writes_one_jsonl_line(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        trace = JsonlQueryTrace(path)
        trace.record("tool_executed", tool="x", duration_ms=12.5)
        trace.close()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        rec = json.loads(lines[0])
        assert rec["kind"] == "tool_executed"
        assert rec["tool"] == "x"
        assert rec["duration_ms"] == 12.5
        # Always-present fields:
        assert "ts" in rec
        assert "trace_id" in rec
        assert rec["schema_version"] == 1

    def test_trace_id_is_stable_across_records(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.jsonl"
        trace = JsonlQueryTrace(path)
        trace.record("classifier_decision", query="X")
        trace.record("tool_executed", tool="y", duration_ms=1.0)
        trace.close()

        recs = [json.loads(line) for line in path.read_text().splitlines()]
        assert recs[0]["trace_id"] == recs[1]["trace_id"]

    def test_file_created_lazily(self, tmp_path: Path) -> None:
        """Opening a trace that's never used must not create the file."""
        path = tmp_path / "never_used.jsonl"
        trace = JsonlQueryTrace(path)
        trace.close()
        assert not path.exists()

    def test_parent_directory_created_on_first_record(self, tmp_path: Path) -> None:
        path = tmp_path / "deep" / "nested" / "trace.jsonl"
        trace = JsonlQueryTrace(path)
        trace.record("tool_executed", tool="x")
        trace.close()
        assert path.exists()

    def test_record_swallows_io_errors(self, tmp_path: Path) -> None:
        """A trace failure must never crash the caller."""
        path = tmp_path / "trace.jsonl"
        trace = JsonlQueryTrace(path)
        trace.record("tool_executed", tool="x")
        # Force an IO failure on the next record by closing the handle.
        trace._handle.close()  # type: ignore[union-attr]
        trace.record("tool_executed", tool="y")  # must not raise
        trace.close()


# ---------------------------------------------------------------------------
# Null trace and factory
# ---------------------------------------------------------------------------


class TestOpenTrace:
    def test_returns_null_trace_when_path_is_none(self) -> None:
        trace = open_trace(None)
        assert isinstance(trace, NullQueryTrace)
        trace.record("anything", x=1)  # no-op, must not raise
        trace.close()

    def test_returns_jsonl_trace_when_path_given(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        trace = open_trace(path)
        assert isinstance(trace, JsonlQueryTrace)
        trace.close()

    def test_context_manager_closes_jsonl_trace(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        with open_trace(path) as trace:
            trace.record("tool_executed", tool="x")
        # File handle is closed; subsequent open is fine.
        path.read_text()


def test_default_trace_path_uses_iso_date_subdir(tmp_path: Path) -> None:
    p = default_trace_path(base_dir=tmp_path)
    # Path shape: <tmp>/traces/<YYYY-MM-DD>/<id>.jsonl
    parts = p.relative_to(tmp_path).parts
    assert parts[0] == "traces"
    assert len(parts[1]) == 10  # ISO date
    assert parts[2].endswith(".jsonl")


def test_trace_path_from_env_returns_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEXUS_TRACE_DIR", raising=False)
    assert trace_path_from_env() is None


def test_trace_path_from_env_uses_env_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("NEXUS_TRACE_DIR", str(tmp_path))
    p = trace_path_from_env()
    assert p is not None
    assert p.is_relative_to(tmp_path)


# ---------------------------------------------------------------------------
# Engine wiring
# ---------------------------------------------------------------------------


def _make_engine_with_graph() -> tuple[QueryEngine, Path]:
    """Build a minimal engine over an in-memory graph."""
    graph = Graph()
    graph.add_node(
        Node(
            id="class:App\\Models\\User",
            kind=NodeKind.MODEL,
            name="User",
            attributes={"fqn": "App\\Models\\User"},
        ),
    )
    handle = MagicMock()
    handle.load.return_value = graph
    storage = MagicMock()
    storage.graph.return_value = handle

    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    return QueryEngine(registry, ctx), Path("/tmp")


def test_engine_emits_tool_executed_record(tmp_path: Path) -> None:
    """A traced ``query()`` call appends one ``tool_executed`` record."""
    engine, _ = _make_engine_with_graph()
    path = tmp_path / "engine.jsonl"
    trace = JsonlQueryTrace(path)
    engine.set_trace(trace)

    engine.query("describe_class", {"fqn": "App\\Models\\User"})
    trace.close()

    recs = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "tool_executed"
    assert rec["tool"] == "describe_class"
    assert rec["args"] == {"fqn": "App\\Models\\User"}
    assert rec["duration_ms"] >= 0
    assert rec["error_code"] is None


def test_engine_records_error_code_on_structured_failure(tmp_path: Path) -> None:
    """When a tool returns ``error_code``, the trace captures it."""
    engine, _ = _make_engine_with_graph()
    path = tmp_path / "err.jsonl"
    trace = JsonlQueryTrace(path)
    engine.set_trace(trace)

    engine.query("describe_class", {"fqn": "App\\Models\\Nonsense"})
    trace.close()

    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["error_code"] == "class_not_found"


def test_engine_with_null_trace_does_not_write(tmp_path: Path) -> None:
    """Default trace is null; no file should ever get created."""
    engine, _ = _make_engine_with_graph()
    # No set_trace() - defaults to NullQueryTrace.
    engine.query("describe_class", {"fqn": "App\\Models\\User"})
    # No file written; nothing to assert besides "no exception."


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def test_record_classifier_decision_serialises_plan(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    trace = JsonlQueryTrace(path)

    plan = QueryClassifier().classify("how does order placement work")
    record_classifier_decision(trace, query="how does order placement work", plan=plan)
    trace.close()

    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["kind"] == "classifier_decision"
    assert rec["query"] == "how does order placement work"
    assert rec["plan"]["tool"] == plan.tool
    assert rec["plan"]["reason"] == plan.reason


def test_record_tool_executed_includes_budget_fields(tmp_path: Path) -> None:
    path = tmp_path / "t.jsonl"
    trace = JsonlQueryTrace(path)
    record_tool_executed(
        trace,
        tool="x",
        args={"a": 1},
        duration_ms=42.0,
        error_code=None,
        result_size=3,
        over_budget=False,
        budget_ms=200,
    )
    trace.close()

    rec = json.loads(path.read_text().splitlines()[0])
    assert rec["budget_ms"] == 200
    assert rec["over_budget"] is False
    assert rec["result_size"] == 3
