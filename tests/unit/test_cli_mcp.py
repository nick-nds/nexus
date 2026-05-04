"""Tests for ``nexus mcp serve``.

Covers both the stdio (default) and the SSE/HTTP transport branches.
The server's ``run`` method is mocked so the test does not actually
start a server.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nexus.interfaces.cli.commands.mcp import mcp_group, serve_command
from nexus.interfaces.cli.context import CliContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, engine: MagicMock | None = None) -> CliContext:
    ctx = CliContext(storage_root=tmp_path, output_format="json")
    if engine is not None:
        ctx._engine = engine  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# nexus mcp serve
# ---------------------------------------------------------------------------


class TestServeCommand:
    def _run_serve(
        self,
        runner: CliRunner,
        tmp_path: Path,
        *extra_args: str,
    ) -> Any:
        """Invoke ``mcp serve`` with a patched FastMCP server.

        Returns (result, mock_mcp) so tests can assert on the mock.
        """

        mock_mcp = MagicMock()
        mock_engine = MagicMock()

        # build_mcp_server is imported locally inside the command; patch
        # it on the source module so both the local import and any cached
        # references see the same mock.
        with patch(
            "nexus.interfaces.mcp.build_mcp_server",
            return_value=mock_mcp,
        ):
            ctx = _make_ctx(tmp_path, engine=mock_engine)
            result = runner.invoke(serve_command, list(extra_args), obj=ctx)

        return result, mock_mcp

    def test_stdio_transport_is_default(self, runner: CliRunner, tmp_path: Path) -> None:
        result, mock_mcp = self._run_serve(runner, tmp_path)

        assert result.exit_code == 0, result.output
        mock_mcp.run.assert_called_once()
        call_kwargs = mock_mcp.run.call_args.kwargs
        assert call_kwargs.get("transport") == "stdio"
        assert call_kwargs.get("show_banner") is False

    def test_sse_transport_passes_host_and_port(self, runner: CliRunner, tmp_path: Path) -> None:
        result, mock_mcp = self._run_serve(
            runner, tmp_path, "--transport", "sse", "--host", "0.0.0.0", "--port", "9000"
        )

        assert result.exit_code == 0, result.output
        mock_mcp.run.assert_called_once()
        call_kwargs = mock_mcp.run.call_args.kwargs
        assert call_kwargs.get("transport") == "sse"
        assert call_kwargs.get("host") == "0.0.0.0"
        assert call_kwargs.get("port") == 9000
        assert call_kwargs.get("show_banner") is False

    def test_http_transport_accepted(self, runner: CliRunner, tmp_path: Path) -> None:
        result, mock_mcp = self._run_serve(runner, tmp_path, "--transport", "http")

        assert result.exit_code == 0, result.output
        call_kwargs = mock_mcp.run.call_args.kwargs
        assert call_kwargs.get("transport") == "http"

    def test_log_level_forwarded_to_non_stdio(self, runner: CliRunner, tmp_path: Path) -> None:
        _result, mock_mcp = self._run_serve(
            runner, tmp_path, "--transport", "sse", "--log-level", "debug"
        )

        call_kwargs = mock_mcp.run.call_args.kwargs
        assert call_kwargs.get("log_level") == "debug"

    def test_mcp_group_has_serve_subcommand(self, runner: CliRunner) -> None:
        result = runner.invoke(mcp_group, ["--help"])
        assert "serve" in result.output

    def test_keyboard_interrupt_exits_cleanly(self, runner: CliRunner, tmp_path: Path) -> None:
        """KeyboardInterrupt (SIGINT) must exit with code 0, no traceback."""
        mock_mcp = MagicMock()
        mock_mcp.run.side_effect = KeyboardInterrupt

        with patch("nexus.interfaces.mcp.build_mcp_server", return_value=mock_mcp):
            ctx = _make_ctx(tmp_path)
            result = runner.invoke(serve_command, [], obj=ctx)

        assert result.exit_code == 0

    def test_sigterm_raises_keyboard_interrupt_and_exits_cleanly(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        """SIGTERM translated to KeyboardInterrupt must also exit with code 0."""
        import signal as _signal

        mock_mcp = MagicMock()
        installed_handlers: list[object] = []

        import contextlib

        def _fake_run(**_kwargs: object) -> None:
            # Simulate SIGTERM arriving while the server is running by
            # calling the handler that serve_command registered.
            handler = installed_handlers[-1]
            if callable(handler):
                with contextlib.suppress(KeyboardInterrupt):
                    handler(_signal.SIGTERM, None)

        mock_mcp.run.side_effect = _fake_run

        original_signal = _signal.signal

        def _capturing_signal(sig: int, handler: object) -> object:
            prev = original_signal(sig, handler)
            installed_handlers.append(handler)
            return prev

        with (
            patch("nexus.interfaces.mcp.build_mcp_server", return_value=mock_mcp),
            patch("nexus.interfaces.cli.commands.mcp.signal.signal", side_effect=_capturing_signal),
        ):
            ctx = _make_ctx(tmp_path)
            result = runner.invoke(serve_command, [], obj=ctx)

        assert result.exit_code == 0
