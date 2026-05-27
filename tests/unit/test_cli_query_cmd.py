"""Tests for the auto-generated ``nexus query`` subcommands.

Covers the internal helpers (_unwrap_optional, _click_type_for,
_iter_options) plus the callback error paths (ToolInputError,
ToolNotFoundError).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from nexus.core.query import (
    QueryEngine,
    ToolInput,
    ToolInputError,
    ToolNotFoundError,
    ToolOutput,
)
from nexus.interfaces.cli.commands.query import (
    _click_type_for,
    _iter_options,
    _make_callback,
    _unwrap_optional,
)
from nexus.interfaces.cli.context import CliContext
from pydantic import Field

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(tmp_path: Path, engine: QueryEngine | None = None) -> CliContext:
    ctx = CliContext(storage_root=tmp_path, output_format="json")
    if engine is not None:
        ctx._engine = engine  # type: ignore[attr-defined]
    return ctx


# ---------------------------------------------------------------------------
# _unwrap_optional
# ---------------------------------------------------------------------------


class TestUnwrapOptional:
    def test_plain_str_is_not_optional(self) -> None:
        typ, optional = _unwrap_optional(str)
        assert typ is str
        assert optional is False

    def test_optional_str_unwraps(self) -> None:
        typ, optional = _unwrap_optional(str | None)
        assert typ is str
        assert optional is True

    def test_optional_int_unwraps(self) -> None:
        typ, optional = _unwrap_optional(int | None)
        assert typ is int
        assert optional is True

    def test_non_optional_union_is_not_unwrapped(self) -> None:
        # str | int is a union but not Optional - should pass through unchanged

        union = str | int  # type: ignore[operator]
        _typ, optional = _unwrap_optional(union)
        assert optional is False


# ---------------------------------------------------------------------------
# _click_type_for
# ---------------------------------------------------------------------------


class TestClickTypeFor:
    def test_str_maps_to_string(self) -> None:
        import click

        assert _click_type_for(str) is click.STRING

    def test_int_maps_to_int(self) -> None:
        import click

        assert _click_type_for(int) is click.INT

    def test_bool_maps_to_bool(self) -> None:
        import click

        assert _click_type_for(bool) is click.BOOL

    def test_float_maps_to_float(self) -> None:
        import click

        assert _click_type_for(float) is click.FLOAT

    def test_unknown_type_returns_none(self) -> None:
        assert _click_type_for(list) is None
        assert _click_type_for(dict) is None


# ---------------------------------------------------------------------------
# _iter_options
# ---------------------------------------------------------------------------


class TestIterOptions:
    def test_unsupported_field_type_raises_runtime_error(self) -> None:
        class _BadInput(ToolInput):
            tags: list[str] = Field(default_factory=list)

        with pytest.raises(RuntimeError, match="unsupported"):
            _iter_options(_BadInput)

    def test_str_field_produces_string_option(self) -> None:
        class _Input(ToolInput):
            fqn: str = Field(description="The FQN.")

        options = _iter_options(_Input)
        assert len(options) == 1
        flag, _field_info, _click_type, required = options[0]
        assert flag == "--fqn"
        assert required is True

    def test_optional_str_field_not_required(self) -> None:
        class _Input(ToolInput):
            method: str | None = Field(default=None, description="HTTP method.")

        options = _iter_options(_Input)
        _, _, _, required = options[0]
        assert required is False


# ---------------------------------------------------------------------------
# _make_callback: error paths
# ---------------------------------------------------------------------------


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestQueryCallback:
    def test_tool_input_error_prints_error_and_exits_2(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.side_effect = ToolInputError("missing required field: fqn")

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        callback = _make_callback("describe_class")

        import click

        cmd = click.Command("describe_class", callback=callback)
        result = runner.invoke(cmd, [], obj=ctx)

        assert result.exit_code == 2

    def test_tool_not_found_error_prints_error_and_exits_2(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.side_effect = ToolNotFoundError("ghost_tool", [])

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        callback = _make_callback("ghost_tool")

        import click

        cmd = click.Command("ghost_tool", callback=callback)
        result = runner.invoke(cmd, [], obj=ctx)

        assert result.exit_code == 2

    def test_successful_query_renders_output(self, runner: CliRunner, tmp_path: Path) -> None:

        from pydantic import Field as _Field

        class _StubOutput(ToolOutput):
            total: int = 0
            truncated: bool = False
            truncated_lists: list[str] = _Field(default_factory=list)

        mock_engine = MagicMock(spec=QueryEngine)
        mock_engine.query.return_value = _StubOutput()

        ctx = _make_ctx(tmp_path, engine=mock_engine)
        callback = _make_callback("list_routes")

        import click

        cmd = click.Command("list_routes", callback=callback)
        result = runner.invoke(cmd, [], obj=ctx)

        assert result.exit_code == 0
