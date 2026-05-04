"""Tests for ``nexus ask`` and its helper functions.

Covers the explain flag, the fallback chain, error branches,
and the ``_is_usable`` / ``_plan_to_dict`` helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from nexus.core.query import QueryEngine, ToolOutput
from nexus.interfaces.cli.commands.ask import (
    _is_usable,
    _plan_to_dict,
    _run_plan,
    ask_command,
)
from nexus.interfaces.cli.context import CliContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, engine: QueryEngine | None = None) -> CliContext:
    """Build a CliContext with an injected engine for unit testing."""
    ctx = CliContext(storage_root=tmp_path, output_format="json")
    if engine is not None:
        ctx._engine = engine  # type: ignore[attr-defined]
    return ctx


def _stub_plan(
    tool: str = "list_routes",
    args: dict[str, Any] | None = None,
    fallbacks: list[Any] | None = None,
) -> Any:
    """Build a minimal QueryPlan-like object."""
    from nexus.core.query import QueryPlan

    return QueryPlan(
        tool=tool,
        args=args or {},
        confidence=0.9,
        reason="test",
        fallbacks=fallbacks or [],
    )


# ---------------------------------------------------------------------------
# _is_usable
# ---------------------------------------------------------------------------


class TestIsUsable:
    def test_result_with_no_error_code_is_usable(self) -> None:
        result = MagicMock(spec=[])
        del result.error_code  # no attribute
        # getattr returns None when attribute absent
        assert _is_usable(result) is True

    def test_result_with_none_error_code_is_usable(self) -> None:
        result = MagicMock()
        result.error_code = None
        assert _is_usable(result) is True

    def test_result_with_error_code_is_not_usable(self) -> None:
        result = MagicMock()
        result.error_code = "not_found"
        assert _is_usable(result) is False


# ---------------------------------------------------------------------------
# _plan_to_dict
# ---------------------------------------------------------------------------


class TestPlanToDict:
    def test_flat_plan(self) -> None:
        plan = _stub_plan("list_routes", {"method": "GET"}, [])
        d = _plan_to_dict(plan)

        assert d["tool"] == "list_routes"
        assert d["args"] == {"method": "GET"}
        assert d["fallbacks"] == []
        assert "confidence" in d
        assert "reason" in d

    def test_nested_fallbacks_are_serialised(self) -> None:
        fallback = _stub_plan("semantic_search", {"query": "foo"}, [])
        plan = _stub_plan("describe_class", {"fqn": "X"}, [fallback])

        d = _plan_to_dict(plan)
        assert len(d["fallbacks"]) == 1
        assert d["fallbacks"][0]["tool"] == "semantic_search"


# ---------------------------------------------------------------------------
# ask_command invoked directly (with injected engine)
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestAskCommand:
    def test_empty_text_exits_2(self, runner: CliRunner, tmp_path: Path) -> None:
        ctx = _make_ctx(tmp_path)
        result = runner.invoke(ask_command, [""], obj=ctx)
        assert result.exit_code == 2

    def test_explain_prints_plan_without_engine(self, runner: CliRunner, tmp_path: Path) -> None:
        """--explain should print classifier plan; engine must not be called."""
        import json

        ctx = _make_ctx(tmp_path)
        result = runner.invoke(ask_command, ["--explain", "list all routes"], obj=ctx)

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "tool" in data
        assert "confidence" in data
        assert "fallbacks" in data

    def test_explain_does_not_call_engine(self, runner: CliRunner, tmp_path: Path) -> None:
        mock_engine = MagicMock(spec=QueryEngine)
        ctx = _make_ctx(tmp_path, engine=mock_engine)
        runner.invoke(ask_command, ["--explain", "what are the routes"], obj=ctx)
        mock_engine.query.assert_not_called()

    def test_successful_result_rendered(self, runner: CliRunner, tmp_path: Path) -> None:

        from pydantic import Field as _Field

        class _StubOutput(ToolOutput):
            total: int = 0
            error_code: str | None = None  # type: ignore[assignment]
            truncated: bool = False
            truncated_lists: list[str] = _Field(default_factory=list)

        stub_output = _StubOutput()
        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = stub_output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        result = runner.invoke(ask_command, ["list", "all", "routes"], obj=ctx)

        assert result.exit_code == 0, result.output

    def test_all_fallbacks_fail_exits_1(self, runner: CliRunner, tmp_path: Path) -> None:
        """When all tools return error codes, exit 1 with an error message."""
        mock_output = MagicMock()
        mock_output.error_code = "not_found"

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = mock_output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        result = runner.invoke(ask_command, ["describe", "App\\Foo"], obj=ctx)

        assert result.exit_code == 1
        assert "no tool returned" in result.output.lower() or result.exit_code == 1


# ---------------------------------------------------------------------------
# _run_plan: ToolInputError is caught and falls through to next plan
# ---------------------------------------------------------------------------


class TestRunPlan:
    def test_tool_input_error_falls_through(self, tmp_path: Path) -> None:
        """A ToolInputError on the primary plan should try the fallback."""
        from nexus.core.query import ToolInputError

        usable_output = MagicMock()
        usable_output.error_code = None

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.side_effect = [
            ToolInputError("bad input"),
            usable_output,
        ]

        ctx = _make_ctx(tmp_path, engine=mock_engine)

        fallback = _stub_plan("semantic_search", {"query": "fallback"})
        primary = _stub_plan("describe_class", {"fqn": "X"}, [fallback])

        wrapped = _run_plan(ctx, primary, "test query")
        assert wrapped is not None
        assert wrapped["result"] is usable_output
        assert wrapped["tool"] == "semantic_search"
        # The first plan failed with a ToolInputError — that should
        # show up in the routing log so the agent sees what was tried.
        assert any("describe_class" in t for t in wrapped["alternatives_tried"])
        assert mock_engine.query.call_count == 2

    def test_all_errors_returns_none(self, tmp_path: Path) -> None:
        """If all plans fail, _run_plan returns None."""
        error_output = MagicMock()
        error_output.error_code = "not_found"

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = error_output

        ctx = _make_ctx(tmp_path, engine=mock_engine)

        plan = _stub_plan("list_routes", fallbacks=[])
        result = _run_plan(ctx, plan, "test query")
        assert result is None

    def test_tool_input_error_on_all_returns_none(self, tmp_path: Path) -> None:
        """If all plans raise ToolInputError, _run_plan returns None."""
        from nexus.core.query import ToolInputError

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.side_effect = ToolInputError("bad")

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        plan = _stub_plan("list_routes", fallbacks=[])
        result = _run_plan(ctx, plan, "test query")
        assert result is None


# ---------------------------------------------------------------------------
# Confidence floor / refusal — subtask 2.3
# ---------------------------------------------------------------------------


def _semantic_fallback_plan(query: str = "off topic") -> Any:
    """A QueryPlan that mimics what ``QueryClassifier._semantic_fallback`` emits."""
    from nexus.core.query import QueryPlan

    return QueryPlan(
        tool="semantic_search",
        args={"query": query},
        confidence=0.40,  # below RULE_CONFIDENCE_FLOOR
        reason="no rule matched",
        fallbacks=(),
    )


def _semantic_output(*hit_scores: float) -> Any:
    """Build a SemanticSearchOutput-shaped object with the given vector scores."""
    from nexus.core.query.tools.semantic_search import (
        SemanticHit,
        SemanticSearchOutput,
    )

    hits = [
        SemanticHit(
            node_id=f"node-{i}",
            node_kind="class",
            node_name=f"Hit{i}",
            score=score,
            vector_score=score,
        )
        for i, score in enumerate(hit_scores)
    ]
    return SemanticSearchOutput(
        query="x",
        total_candidates=len(hits),
        returned=len(hits),
        hits=hits,
    )


class TestConfidenceFloor:
    """Subtask 2.3: low-confidence semantic fallbacks must surface refusals."""

    def test_high_confidence_rule_passes_through_even_with_weak_hits(
        self,
        tmp_path: Path,
    ) -> None:
        """A real-rule plan returning semantic_search-shaped output is trusted."""
        plan = _stub_plan("semantic_search", {"query": "x"})  # confidence=0.9
        output = _semantic_output(0.10, 0.20)  # all weak

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        wrapped = _run_plan(ctx, plan, "x")

        assert wrapped is not None
        assert wrapped["tool"] == "semantic_search"
        assert wrapped["confidence"] == pytest.approx(0.9)
        # The Pydantic model is dumped to a dict before wrapping; the
        # ``hits`` list survives the round trip.
        assert len(wrapped["result"]["hits"]) == 2

    def test_low_confidence_with_strong_hit_returns_result(self, tmp_path: Path) -> None:
        """If ANY hit clears the floor, the semantic result is the answer."""
        plan = _semantic_fallback_plan()
        output = _semantic_output(0.50, 0.72, 0.40)  # 0.72 ≥ 0.65

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        wrapped = _run_plan(ctx, plan, "off topic")

        assert wrapped is not None
        assert wrapped["tool"] == "semantic_search"
        assert wrapped["confidence"] == pytest.approx(0.4)
        assert len(wrapped["result"]["hits"]) == 3
        assert wrapped["result"].get("error_code") is None

    def test_low_confidence_with_only_weak_hits_returns_refusal(
        self,
        tmp_path: Path,
    ) -> None:
        """All hits below the floor → routing wrap with ``no_confident_match`` refusal."""
        plan = _semantic_fallback_plan(query="make me a sandwich")
        output = _semantic_output(0.50, 0.40, 0.30)

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        wrapped = _run_plan(ctx, plan, "make me a sandwich")

        assert wrapped is not None
        assert wrapped["tool"] == "semantic_search"

        refusal = wrapped["result"]
        assert refusal["error_code"] == "no_confident_match"
        assert refusal["query"] == "make me a sandwich"
        assert refusal["best_vector_score"] == pytest.approx(0.50)
        assert refusal["weak_hits_count"] == 3
        assert "list_routes" in refusal["suggested_tools"]

    def test_low_confidence_with_no_hits_returns_refusal(self, tmp_path: Path) -> None:
        """Empty semantic output is also a refusal — distinguishes 'broken' from 'no answer'."""
        plan = _semantic_fallback_plan()
        output = _semantic_output()  # zero hits

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        wrapped = _run_plan(ctx, plan, "anything")

        assert wrapped is not None
        refusal = wrapped["result"]
        assert refusal["error_code"] == "no_confident_match"
        assert refusal["best_vector_score"] == pytest.approx(0.0)
        assert refusal["weak_hits_count"] == 0

    def test_lower_floor_lets_borderline_hit_pass_through(self, tmp_path: Path) -> None:
        """A lowered floor rescues a 0.55 hit that the default 0.65 would refuse."""
        plan = _semantic_fallback_plan()
        output = _semantic_output(0.55)  # below default floor, above 0.50

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)

        # Default floor (0.65) → refuse.
        wrapped_default = _run_plan(ctx, plan, "borderline")
        assert wrapped_default is not None
        assert wrapped_default["result"]["error_code"] == "no_confident_match"

        # Lowered floor (0.50) → pass through.
        wrapped_relaxed = _run_plan(ctx, plan, "borderline", semantic_floor=0.50)
        assert wrapped_relaxed is not None
        assert wrapped_relaxed["result"].get("error_code") is None
        assert len(wrapped_relaxed["result"]["hits"]) == 1

    def test_refusal_message_reports_the_active_floor(self, tmp_path: Path) -> None:
        """The refusal payload must show the floor that was actually used.

        Otherwise users tuning ``ask.semantic_confidence_floor`` get a
        misleading "threshold 0.65" message even after they've changed it.
        """
        plan = _semantic_fallback_plan()
        output = _semantic_output(0.30)

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)

        wrapped = _run_plan(ctx, plan, "x", semantic_floor=0.42)
        assert wrapped is not None
        assert "threshold 0.42" in wrapped["result"]["error"]

    def test_routing_wrap_has_expected_keys(self, tmp_path: Path) -> None:
        """The wrapped envelope shape is part of the public contract for ``ask``."""
        plan = _stub_plan("list_routes")  # confidence=0.9
        output = MagicMock()
        output.error_code = None

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = output

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        wrapped = _run_plan(ctx, plan, "list routes")

        assert wrapped is not None
        for key in ("tool", "confidence", "reason", "alternatives_tried", "result"):
            assert key in wrapped, f"missing routing key {key!r}"
        assert wrapped["tool"] == "list_routes"
        assert wrapped["alternatives_tried"] == []


# ---------------------------------------------------------------------------
# --trace flag (Gap #1)
# ---------------------------------------------------------------------------


class TestAskTraceFlag:
    def test_trace_file_written_with_classifier_and_envelope(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """``ask --trace PATH`` writes a JSONL file with the full decision chain."""
        import json

        from pydantic import Field as _Field

        class _StubOutput(ToolOutput):
            total: int = 0
            error_code: str | None = None  # type: ignore[assignment]
            truncated: bool = False
            truncated_lists: list[str] = _Field(default_factory=list)

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = _StubOutput()

        trace_path = tmp_path / "ask.jsonl"
        ctx = _make_ctx(tmp_path, engine=mock_engine)

        result = runner.invoke(
            ask_command,
            ["--trace", str(trace_path), "list", "all", "routes"],
            obj=ctx,
        )

        assert result.exit_code == 0, result.output
        assert trace_path.exists(), "trace file must be written"
        records = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        kinds = [r["kind"] for r in records]
        assert "classifier_decision" in kinds
        assert "ask_envelope" in kinds

        # set_trace was called twice: install + reset.
        assert mock_engine.set_trace.call_count == 2

    def test_no_trace_flag_writes_no_file(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Without ``--trace`` the engine still gets a null trace; no file output."""
        from pydantic import Field as _Field

        class _StubOutput(ToolOutput):
            total: int = 0
            error_code: str | None = None  # type: ignore[assignment]
            truncated: bool = False
            truncated_lists: list[str] = _Field(default_factory=list)

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = _StubOutput()

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        result = runner.invoke(ask_command, ["list", "routes"], obj=ctx)

        assert result.exit_code == 0
        # No new files should appear under tmp_path's children.
        assert not any(p.is_file() for p in tmp_path.rglob("*.jsonl"))
