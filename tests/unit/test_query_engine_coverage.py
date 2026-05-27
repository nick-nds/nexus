"""Verify :class:`QueryEngine` attaches :class:`Coverage` to every tool result.

Exercised through a tiny stub tool so the assertion is about the
engine's behaviour, not any specific tool's payload shape.
"""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import MagicMock

from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.engine import QueryEngine
from nexus.core.query.registry import ToolRegistry
from nexus.core.query.tool_protocol import ToolInput, ToolOutput


class _StubInput(ToolInput):
    """Empty input model - the stub tool ignores the payload."""


class _StubOutput(ToolOutput):
    answer: str = "default"


class _StubTool:
    name: ClassVar[str] = "stub"
    description: ClassVar[str] = "A test tool that returns a fixed string."
    input_model: ClassVar[type[ToolInput]] = _StubInput
    output_model: ClassVar[type[ToolOutput]] = _StubOutput
    latency_budget_ms: ClassVar[int] = 1000

    def execute(self, payload: ToolInput, ctx: QueryContext) -> ToolOutput:
        _ = payload, ctx
        # Tools never set ``coverage`` themselves - the engine does.
        return _StubOutput(answer="hello")


def _make_engine(coverage: Coverage | None) -> QueryEngine:
    registry = ToolRegistry()
    registry.register(_StubTool)
    storage = MagicMock(spec=[])
    ctx = QueryContext(
        storage=storage,  # type: ignore[arg-type]
        budget=ResponseBudget(),
        coverage=coverage,
    )
    return QueryEngine(registry, ctx)


def test_engine_attaches_coverage_to_tool_output() -> None:
    """A populated context coverage is stamped onto every output."""
    coverage = Coverage(
        calls_indexed=True,
        lsp_server="/usr/local/bin/intelephense",
        embedder_id="ollama:nomic-embed-text",
        indexed_at="2026-05-03T12:00:00+00:00",
        project_path="/tmp/x",
    )
    engine = _make_engine(coverage)

    output = engine.query("stub", {})

    assert isinstance(output, _StubOutput)
    assert output.answer == "hello"
    assert output.coverage == coverage


def test_engine_passes_through_when_context_has_no_coverage() -> None:
    """Coverage is optional - without it the output has the default ``None``."""
    engine = _make_engine(coverage=None)

    output = engine.query("stub", {})

    assert output.coverage is None


def test_coverage_round_trips_through_json() -> None:
    """The coverage block survives serialisation to JSON and back."""
    coverage = Coverage(
        calls_indexed=False,
        embedder_id="fastembed",
        indexed_at="2026-05-01T00:00:00+00:00",
    )
    engine = _make_engine(coverage)

    output = engine.query("stub", {})
    json_str = output.model_dump_json()
    restored = _StubOutput.model_validate_json(json_str)

    assert restored.coverage == coverage
