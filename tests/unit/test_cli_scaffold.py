"""Tests for the CLI scaffold, output rendering, and context."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.context import (
    DEFAULT_ROOT,
    DEFAULT_SLUG,
    CliContext,
    OutputFormat,
)
from nexus.interfaces.cli.main import main
from nexus.version import __version__


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


class TestRootGroup:
    def test_version_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_help_lists_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "query" in result.output
        assert "ask" in result.output

    def test_query_subgroup_has_every_registered_tool(
        self,
        runner: CliRunner,
    ) -> None:
        from nexus.core.query import ToolRegistry
        from nexus.core.query.tools import register_builtin_tools

        registry = ToolRegistry()
        register_builtin_tools(registry)

        result = runner.invoke(main, ["query", "--help"])
        assert result.exit_code == 0
        for name in registry.names():
            assert name in result.output

    def test_per_tool_help_includes_description(
        self,
        runner: CliRunner,
    ) -> None:
        result = runner.invoke(main, ["query", "trace_route", "--help"])
        assert result.exit_code == 0
        assert "--method" in result.output
        assert "--uri" in result.output
        assert "route" in result.output.lower()

    def test_required_option_is_enforced(self, runner: CliRunner) -> None:
        # find_implementations requires --interface-fqn
        result = runner.invoke(main, ["query", "find_implementations"])
        assert result.exit_code != 0
        assert "--interface-fqn" in result.output


# ---------------------------------------------------------------------------
# CliContext
# ---------------------------------------------------------------------------


class TestCliContext:
    def test_defaults(self) -> None:
        ctx = CliContext()
        assert ctx.storage_root == DEFAULT_ROOT
        assert ctx.project_slug == DEFAULT_SLUG
        assert ctx.output_format == OutputFormat.AUTO

    def test_resolved_format_respects_explicit(self) -> None:
        ctx = CliContext(output_format=OutputFormat.JSON)
        assert ctx.resolved_format() == OutputFormat.JSON

        ctx = CliContext(output_format=OutputFormat.PRETTY)
        assert ctx.resolved_format() == OutputFormat.PRETTY

    def test_use_color_explicit_wins(self) -> None:
        ctx = CliContext(color=True)
        assert ctx.use_color() is True

        ctx = CliContext(color=False)
        assert ctx.use_color() is False

    def test_use_color_respects_no_color_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NO_COLOR", "1")
        ctx = CliContext()  # color unset → defer to env
        assert ctx.use_color() is False

    def test_close_is_idempotent(self) -> None:
        ctx = CliContext()
        ctx.close()
        ctx.close()


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


class TestOutput:
    def test_json_mode_prints_valid_json(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from nexus.core.query.tools.list_routes import ListRoutesOutput
        from nexus.interfaces.cli.output import render

        ctx = CliContext(output_format=OutputFormat.JSON)
        payload = ListRoutesOutput(total=0, returned=0, routes=[])

        render(ctx, payload)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["total"] == 0
        assert parsed["routes"] == []

    def test_json_mode_uses_field_aliases(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from nexus.core.query.tools.find_handlers import HandlerRow
        from nexus.interfaces.cli.output import render

        # HandlerRow has `method_name` aliased to `method`.
        row = HandlerRow(
            route_id="r1",
            uri="/x",
            methods=["GET"],
            action_kind="controller",
            method="show",
        )
        ctx = CliContext(output_format=OutputFormat.JSON)

        render(ctx, row)

        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["method"] == "show"
        assert "method_name" not in parsed

    def test_plain_string_passes_through(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from nexus.interfaces.cli.output import render

        ctx = CliContext(output_format=OutputFormat.JSON)
        render(ctx, "just a message")

        assert "just a message" in capsys.readouterr().out

    def test_print_error_goes_to_stderr(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from nexus.interfaces.cli.output import print_error

        ctx = CliContext(color=False)
        print_error(ctx, "something broke", hint="try --help")

        captured = capsys.readouterr()
        assert "something broke" in captured.err
        assert "hint: try --help" in captured.err
        assert captured.out == ""


# ---------------------------------------------------------------------------
# End-to-end: `nexus query list_routes` against a fresh empty index
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_list_routes_empty_storage(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        # A brand-new storage root has no projects/default dir;
        # list_routes should still succeed with total=0.
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / ".nexus"),
                "--slug",
                "empty",
                "--format",
                "json",
                "query",
                "list_routes",
            ],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["total"] == 0


# ---------------------------------------------------------------------------
# nexus ask
# ---------------------------------------------------------------------------


class TestAsk:
    def test_explain_prints_the_plan(self, runner: CliRunner) -> None:
        result = runner.invoke(
            main,
            ["--format", "json", "ask", "--explain", "POST", "/api/orders"],
        )
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["tool"] == "trace_route"
        assert parsed["args"] == {"method": "POST", "uri": "/api/orders"}

    def test_ask_empty_query_errors(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["ask", " "])
        # Click treats a single space as the positional token, so
        # strip() inside the command returns empty and we bail out.
        assert result.exit_code == 2

    def test_ask_runs_primary_plan(
        self,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        result = runner.invoke(
            main,
            [
                "--storage-root",
                str(tmp_path / ".nexus"),
                "--slug",
                "empty",
                "--format",
                "json",
                "ask",
                "show",
                "all",
                "routes",
            ],
        )
        # list_routes on an empty store returns total=0 with no error,
        # which is "usable" per the ask command's definition. ``ask``
        # wraps the result with the routing decision (subtask 2.4),
        # so the tool output sits under ``parsed["result"]``.
        assert result.exit_code == 0, result.output
        parsed = json.loads(result.output)
        assert parsed["tool"] == "list_routes"
        assert parsed["result"]["total"] == 0
