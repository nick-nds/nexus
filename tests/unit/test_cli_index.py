"""Tests for `nexus index` subcommands and the progress reporters."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.main import main
from nexus.interfaces.cli.progress import JsonLinesProgressReporter, RichProgressReporter
from nexus.pipeline.progress import (
    PassFinished,
    PassProgress,
    PassStarted,
    PipelineFinished,
)


def _null_console() -> Any:
    """Return a Rich Console backed by a StringIO (no real file handles)."""
    from rich.console import Console

    return Console(file=io.StringIO(), highlight=False)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# JsonLinesProgressReporter
# ---------------------------------------------------------------------------


class TestJsonLinesProgressReporter:
    def _collect(self, *events: Any) -> list[dict[str, Any]]:
        """Emit events and return the parsed JSON lines."""
        import io

        buf = io.StringIO()
        reporter = JsonLinesProgressReporter(file=buf)
        for e in events:
            reporter.emit(e)
        buf.seek(0)
        return [json.loads(line) for line in buf if line.strip()]

    def test_pass_started(self) -> None:
        lines = self._collect(PassStarted(pass_name="extract"))
        assert lines == [{"event": "PassStarted", "pass": "extract"}]

    def test_pass_progress_minimal(self) -> None:
        lines = self._collect(PassProgress(pass_name="build", message="scanning"))
        assert lines[0]["event"] == "PassProgress"
        assert lines[0]["pass"] == "build"
        assert lines[0]["message"] == "scanning"
        assert "current" not in lines[0]
        assert "total" not in lines[0]

    def test_pass_progress_with_counters(self) -> None:
        lines = self._collect(PassProgress(pass_name="embed", message="ok", current=5, total=10))
        assert lines[0]["current"] == 5
        assert lines[0]["total"] == 10

    def test_pass_finished_ok(self) -> None:
        lines = self._collect(PassFinished(pass_name="chunk", ok=True, duration_ms=42.5))
        assert lines[0]["ok"] is True
        assert lines[0]["duration_ms"] == 42.5
        assert lines[0]["warnings"] == 0
        assert lines[0]["errors"] == 0

    def test_pass_finished_failure(self) -> None:
        lines = self._collect(
            PassFinished(pass_name="chunk", ok=False, duration_ms=10.0, errors=2, warnings=1)
        )
        assert lines[0]["ok"] is False
        assert lines[0]["errors"] == 2
        assert lines[0]["warnings"] == 1

    def test_pipeline_finished(self) -> None:
        lines = self._collect(PipelineFinished(ok=True, duration_ms=1234.0, passes_completed=4))
        assert lines[0]["event"] == "PipelineFinished"
        assert lines[0]["ok"] is True
        assert lines[0]["passes_completed"] == 4

    def test_full_sequence_produces_one_line_per_event(self) -> None:
        lines = self._collect(
            PassStarted("extract"),
            PassProgress("extract", "running", current=1, total=3),
            PassFinished("extract", ok=True, duration_ms=50.0),
            PipelineFinished(ok=True, duration_ms=100.0, passes_completed=1),
        )
        assert len(lines) == 4

    def test_swallows_rendering_exceptions(self) -> None:
        """Reporter must not raise even if file.write raises."""
        import io

        buf = io.StringIO()
        buf.close()  # force write to fail
        reporter = JsonLinesProgressReporter(file=buf)
        # Should not raise
        reporter.emit(PassStarted("x"))

    def test_defaults_to_stderr(self) -> None:
        import sys

        reporter = JsonLinesProgressReporter()
        assert reporter._file is sys.stderr

    def test_swallowed_exception_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per Gap #4: failures should leave a structured-log breadcrumb."""
        from nexus.interfaces.cli import progress as progress_module

        captured: list[tuple[str, dict[str, Any]]] = []

        class _StubLog:
            def warning(self, event: str, **kw: Any) -> None:
                captured.append((event, kw))

        monkeypatch.setattr(progress_module, "log", _StubLog())

        buf = io.StringIO()
        buf.close()  # force write to fail
        reporter = JsonLinesProgressReporter(file=buf)
        reporter.emit(PassStarted("x"))

        assert captured, "expected a warning log on rendering failure"
        event_name, kw = captured[0]
        assert event_name == "progress_emit_failed"
        assert kw["reporter"] == "jsonl"
        assert kw["event_type"] == "PassStarted"


# ---------------------------------------------------------------------------
# RichProgressReporter
# ---------------------------------------------------------------------------


class TestRichProgressReporter:
    def test_context_manager_starts_and_stops_progress(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        assert not reporter._active
        with reporter:
            assert reporter._active
        assert not reporter._active

    def test_emit_outside_context_does_not_raise(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        # No context manager — must not raise
        reporter.emit(PassStarted("test"))

    def test_pass_started_creates_task(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            reporter.emit(PassStarted("my_pass"))
            assert "my_pass" in reporter._task_ids

    def test_pass_progress_updates_task(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            reporter.emit(PassStarted("my_pass"))
            reporter.emit(PassProgress("my_pass", message="working", current=3, total=10))
            tid = reporter._task_ids["my_pass"]
            task = reporter._progress.tasks[tid]
            assert task.completed == 3
            assert task.total == 10

    def test_pass_progress_unknown_pass_ignored(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            # No PassStarted — should not raise
            reporter.emit(PassProgress("ghost", message="?"))

    def test_pass_finished_completes_task(self) -> None:
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            reporter.emit(PassStarted("p"))
            reporter.emit(PassProgress("p", "running", current=2, total=5))
            reporter.emit(PassFinished("p", ok=True, duration_ms=99.0))
            tid = reporter._task_ids["p"]
            task = reporter._progress.tasks[tid]
            assert task.completed == task.total

    def test_swallows_rendering_exceptions(self) -> None:
        """An error inside rich must not propagate out of emit()."""
        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            # Inject a bad task id to force an error inside _emit
            reporter._task_ids["bogus"] = -9999  # type: ignore[assignment]
            reporter.emit(PassFinished("bogus", ok=True, duration_ms=1.0))

    def test_swallowed_exception_logs_warning(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Per Gap #4: failures should leave a structured-log breadcrumb."""
        from nexus.interfaces.cli import progress as progress_module

        captured: list[tuple[str, dict[str, Any]]] = []

        class _StubLog:
            def warning(self, event: str, **kw: Any) -> None:
                captured.append((event, kw))

        monkeypatch.setattr(progress_module, "log", _StubLog())

        console = _null_console()
        reporter = RichProgressReporter(console=console)
        with reporter:
            reporter._task_ids["bogus"] = -9999  # type: ignore[assignment]
            reporter.emit(PassFinished("bogus", ok=True, duration_ms=1.0))

        assert captured, "expected a warning log on rendering failure"
        event_name, kw = captured[0]
        assert event_name == "progress_emit_failed"
        assert kw["reporter"] == "rich"
        assert kw["event_type"] == "PassFinished"


# ---------------------------------------------------------------------------
# `nexus index` CLI surface tests
# ---------------------------------------------------------------------------


class TestIndexGroup:
    def test_index_help_lists_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["index", "--help"])
        assert result.exit_code == 0
        for sub in ("rebuild", "sync", "status", "clear"):
            assert sub in result.output

    def test_status_exits_1_when_no_index(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--slug",
                "test-proj",
                "--format",
                "json",
                "index",
                "status",
            ],
        )
        assert result.exit_code == 1

    def test_clear_nothing_to_clear(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / "storage"),
                "--slug",
                "test-proj",
                "--format",
                "json",
                "index",
                "clear",
                "--force",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "nothing to clear"

    def test_clear_with_force_removes_dir(self, runner: CliRunner, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        slug = "test-proj"
        project_dir = storage_root / "projects" / slug
        project_dir.mkdir(parents=True)
        (project_dir / "dummy.db").write_text("x")

        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(storage_root),
                "--slug",
                slug,
                "--format",
                "json",
                "index",
                "clear",
                "--force",
            ],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["status"] == "cleared"
        assert not project_dir.exists()

    def test_clear_requires_confirm_without_force(self, runner: CliRunner, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        slug = "test-proj"
        project_dir = storage_root / "projects" / slug
        project_dir.mkdir(parents=True)

        # Provide 'n' to abort the confirmation
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(storage_root),
                "--slug",
                slug,
                "--format",
                "json",
                "index",
                "clear",
            ],
            input="n\n",
        )
        assert result.exit_code != 0
        assert project_dir.exists()

    def test_clear_yes_flag_skips_confirm(self, runner: CliRunner, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        slug = "test-proj"
        project_dir = storage_root / "projects" / slug
        project_dir.mkdir(parents=True)

        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(storage_root),
                "--slug",
                slug,
                "--yes",
                "--format",
                "json",
                "index",
                "clear",
            ],
        )
        assert result.exit_code == 0
        assert not project_dir.exists()

    def test_rebuild_extractor_missing_exits_2(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.adapters.extractor import ExtractorMissingError

        with patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build:
            mock_pipeline = MagicMock()
            mock_pipeline.run.side_effect = ExtractorMissingError("not found")
            mock_build.return_value = mock_pipeline

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 2

    def test_rebuild_extractor_timeout_exits_1(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.adapters.extractor import ExtractorTimeoutError

        with patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build:
            mock_pipeline = MagicMock()
            mock_pipeline.run.side_effect = ExtractorTimeoutError("timed out")
            mock_build.return_value = mock_pipeline

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1

    def test_rebuild_pipeline_failure_exits_1(self, runner: CliRunner, tmp_path: Path) -> None:
        from nexus.pipeline import PipelineResult

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = False

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 1

    def test_rebuild_warns_when_lsp_missing(self, runner: CliRunner, tmp_path: Path) -> None:
        """When no LSP is available the rebuild succeeds with a warning on stderr."""
        from nexus.pipeline import PipelineResult

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        # Point the Mason fallback at an empty tmp dir so the resolver
        # doesn't accidentally find a real LSP server installed on the
        # host running this test.
        empty_home = tmp_path / "fake-home"
        empty_home.mkdir()

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=empty_home),
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )

        # Rebuild must succeed despite missing LSP.
        # CliRunner merges stdout + stderr into result.output.
        assert result.exit_code == 0
        assert "WARNING" in result.output
        assert "LSP" in result.output

    def test_rebuild_lsp_none_skips_lsp_without_warning(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """``--lsp none`` opts out of CALLS enrichment without emitting a warning."""
        from nexus.pipeline import PipelineResult

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                    "--lsp",
                    "none",
                ],
            )

        assert result.exit_code == 0
        assert "WARNING" not in result.output

    def test_rebuild_lsp_unknown_binary_exits_user_action_required(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """An explicit ``--lsp <name>`` that can't be resolved exits 2 (user action)."""
        with (
            patch("shutil.which", return_value=None),
            patch("pathlib.Path.home", return_value=tmp_path / "fake-home"),
        ):
            (tmp_path / "fake-home").mkdir()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                    "--lsp",
                    "non-existent-lsp",
                ],
            )

        assert result.exit_code == 2
        assert "non-existent-lsp" in result.output

    def test_rebuild_cost_prompt_shown_for_paid_embedder(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """Cost estimate is shown and confirmation required for paid providers."""
        from nexus.pipeline import PipelineResult

        # Write a global config that uses voyage as embedder
        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True)
        # cost.confirm_above_usd: 0.0 means "always ask" regardless of estimate size
        (storage_root / "config.yml").write_text(
            "schema_version: '1.0'\nembedder:\n  provider: voyage\n"
            "cost:\n  confirm_above_usd: 0.0\n"
        )

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            # Answer "n" to the cost confirmation prompt → expect abort
            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(storage_root),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
                input="n\n",  # decline the cost prompt
            )

        # The prompt text should appear in output
        assert "Estimated embedding cost" in result.output
        assert "voyage" in result.output

    def test_rebuild_cost_prompt_bypassed_with_yes_flag(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """--yes bypasses the cost confirmation for paid embedders."""
        from nexus.pipeline import PipelineResult

        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True)
        (storage_root / "config.yml").write_text(
            "schema_version: '1.0'\nembedder:\n  provider: voyage\n"
            "cost:\n  confirm_above_usd: 0.0\n"
        )

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(storage_root),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "--yes",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0

    def test_rebuild_no_cost_prompt_for_free_embedder(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """No cost prompt for local/free providers (fastembed, ollama)."""
        from nexus.pipeline import PipelineResult

        storage_root = tmp_path / "storage"
        storage_root.mkdir(parents=True)
        # Default provider is sentence_transformers (free)

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(storage_root),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0
        assert "Estimated embedding cost" not in result.output

    def test_rebuild_no_lsp_warning_when_lsp_present(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """When intelephense is installed no LSP warning is emitted."""
        from nexus.pipeline import PipelineResult

        mock_result = MagicMock(spec=PipelineResult)
        mock_result.ok = True

        with (
            patch("nexus.interfaces.cli.commands._index_helpers._build_pipeline") as mock_build,
            patch("nexus.interfaces.cli.commands._index_helpers._detect_profile") as mock_profile,
            patch(
                "shutil.which",
                side_effect=lambda n: (
                    "/usr/local/bin/intelephense" if n == "intelephense" else None
                ),
            ),
        ):
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = mock_result
            mock_build.return_value = mock_pipeline
            mock_profile.return_value = MagicMock()

            result = runner.invoke(
                main,
                [
                    "--storage-root",
                    str(tmp_path / "storage"),
                    "--slug",
                    "proj",
                    "--format",
                    "json",
                    "index",
                    "rebuild",
                    "--project-path",
                    str(tmp_path),
                ],
            )

        assert result.exit_code == 0
        assert "LSP" not in result.output
