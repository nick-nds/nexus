"""Tests for the pipeline skeleton: context, orchestrator, progress events."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from nexus.core.outcome import Error, Warning
from nexus.pipeline.context import PipelineContext
from nexus.pipeline.orchestrator import Pipeline, PipelineResult
from nexus.pipeline.progress import (
    CollectingProgressReporter,
    LoggingProgressReporter,
    NullProgressReporter,
    PassFinished,
    PassProgress,
    PassStarted,
    PipelineFinished,
)

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StubProfile:
    """Minimal Profile structural stub."""

    name: str = "stub"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


class _StubStorage:
    """Bare stand-in for ProjectStorage so we don't touch disk."""


def make_context(tmp_path: Path) -> PipelineContext:
    return PipelineContext(
        project_path=tmp_path,
        storage=_StubStorage(),  # type: ignore[arg-type]
        profile=StubProfile(),
        progress=CollectingProgressReporter(),
    )


class StubPass:
    """Minimal pass used to drive the orchestrator in tests."""

    def __init__(
        self,
        name: str,
        behaviour: str = "ok",
        message: str = "",
    ) -> None:
        self.name = name
        self._behaviour = behaviour
        self._message = message or name

    def run(self, ctx: PipelineContext) -> None:
        if self._behaviour == "warn":
            ctx.add_warning(Warning(code="stub_warn", message=self._message))
        elif self._behaviour == "error":
            ctx.add_error(Error(code="stub_error", message=self._message))
        elif self._behaviour == "crash":
            raise RuntimeError(self._message)
        # "ok" falls through — no side effect


# ---------------------------------------------------------------------------
# Progress events
# ---------------------------------------------------------------------------


class TestProgressEventsAreFrozen:
    def test_pass_started_is_frozen(self) -> None:
        event = PassStarted(pass_name="x")
        with pytest.raises((AttributeError, TypeError)):
            event.pass_name = "y"  # type: ignore[misc]

    def test_collecting_reporter_accumulates_events(self) -> None:
        reporter = CollectingProgressReporter()
        reporter.emit(PassStarted(pass_name="a"))
        reporter.emit(PassProgress(pass_name="a", message="half"))
        reporter.emit(PassFinished(pass_name="a", ok=True, duration_ms=1.0))

        assert len(reporter.events) == 3
        starts = list(reporter.events_of_type(PassStarted))
        finishes = list(reporter.events_of_type(PassFinished))
        assert len(starts) == 1
        assert len(finishes) == 1


class TestNullReporterIsNoOp:
    def test_emit_discards(self) -> None:
        reporter = NullProgressReporter()
        reporter.emit(PassStarted(pass_name="x"))  # should not raise


class TestLoggingReporter:
    def test_emits_without_raising(self) -> None:
        reporter = LoggingProgressReporter()
        reporter.emit(PassStarted(pass_name="x"))
        reporter.emit(PassProgress(pass_name="x", message="update", current=1, total=5))
        reporter.emit(PassFinished(pass_name="x", ok=True, duration_ms=2.5))
        reporter.emit(PassFinished(pass_name="y", ok=False, duration_ms=1.0, errors=1))
        reporter.emit(PipelineFinished(ok=True, duration_ms=10.0, passes_completed=1))
        reporter.emit(PipelineFinished(ok=False, duration_ms=10.0, passes_completed=1))


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


class TestPipelineContext:
    def test_defaults(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)

        assert ctx.ok()
        assert ctx.warnings == []
        assert ctx.errors == []
        assert ctx.graph is None
        assert ctx.reflection is None

    def test_add_warning_does_not_flip_ok(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        ctx.add_warning(Warning(code="w", message="hi"))

        assert ctx.ok()
        assert len(ctx.warnings) == 1

    def test_add_error_flips_ok(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        ctx.add_error(Error(code="e", message="boom"))

        assert not ctx.ok()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class TestOrchestrator:
    def test_runs_passes_in_order(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        pipeline = Pipeline([StubPass("first"), StubPass("second"), StubPass("third")])

        result = pipeline.run(ctx)

        assert result.ok
        assert result.passes_run == ("first", "second", "third")
        assert set(result.pass_durations_ms) == {"first", "second", "third"}

    def test_stops_at_first_error(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        pipeline = Pipeline(
            [
                StubPass("first"),
                StubPass("second", behaviour="error", message="nope"),
                StubPass("third"),
            ],
        )

        result = pipeline.run(ctx)

        assert not result.ok
        assert result.passes_run == ("first", "second")
        assert "third" not in result.passes_run

    def test_crash_is_recorded_as_error(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        pipeline = Pipeline(
            [
                StubPass("first"),
                StubPass("second", behaviour="crash", message="kaboom"),
                StubPass("third"),
            ],
        )

        result = pipeline.run(ctx)

        assert not result.ok
        assert result.crashed_pass == "second"
        # The orchestrator appended a pass_crashed error to the context.
        crash_errors = [e for e in ctx.errors if e.code == "pass_crashed"]
        assert len(crash_errors) == 1
        assert "kaboom" in crash_errors[0].message
        # Traceback is preserved for post-mortem.
        assert "traceback" in crash_errors[0].context

    def test_warnings_do_not_stop_the_pipeline(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        pipeline = Pipeline(
            [
                StubPass("first", behaviour="warn"),
                StubPass("second", behaviour="warn"),
                StubPass("third"),
            ],
        )

        result = pipeline.run(ctx)

        assert result.ok
        assert result.passes_run == ("first", "second", "third")
        assert len(ctx.warnings) == 2

    def test_empty_pipeline_is_ok(self, tmp_path: Path) -> None:
        ctx = make_context(tmp_path)
        pipeline = Pipeline([])

        result = pipeline.run(ctx)

        assert result.ok
        assert result.passes_run == ()

    def test_progress_events_emitted_per_pass(self, tmp_path: Path) -> None:
        reporter = CollectingProgressReporter()
        ctx = PipelineContext(
            project_path=tmp_path,
            storage=_StubStorage(),  # type: ignore[arg-type]
            profile=StubProfile(),
            progress=reporter,
        )
        pipeline = Pipeline([StubPass("a"), StubPass("b")])

        pipeline.run(ctx)

        starts = [e for e in reporter.events if isinstance(e, PassStarted)]
        finishes = [e for e in reporter.events if isinstance(e, PassFinished)]
        pipelines = [e for e in reporter.events if isinstance(e, PipelineFinished)]

        assert [e.pass_name for e in starts] == ["a", "b"]
        assert [e.pass_name for e in finishes] == ["a", "b"]
        assert len(pipelines) == 1
        assert pipelines[0].passes_completed == 2

    def test_result_is_frozen(self, tmp_path: Path) -> None:
        pipeline = Pipeline([])
        ctx = make_context(tmp_path)
        result: Any = pipeline.run(ctx)

        assert isinstance(result, PipelineResult)
        with pytest.raises((AttributeError, TypeError)):
            result.ok = False
