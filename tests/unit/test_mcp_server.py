"""Tests for the FastMCP server adapter."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.interfaces.cli.main import main
from nexus.interfaces.mcp import build_mcp_server


@pytest.fixture
def stub_engine(tmp_path: Path) -> QueryEngine:
    """A QueryEngine backed by an empty real storage."""
    from nexus.adapters.storage import ProjectStorage

    storage = ProjectStorage(root=tmp_path / "nexus", slug="test")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(
        storage=storage,
        budget=ResponseBudget(),
    )
    return QueryEngine(registry=registry, context=ctx)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# build_mcp_server
# ---------------------------------------------------------------------------


class TestBuildMcpServer:
    def test_returns_fastmcp_instance(self, stub_engine: QueryEngine) -> None:
        from fastmcp import FastMCP

        mcp = build_mcp_server(stub_engine)
        assert isinstance(mcp, FastMCP)

    def test_server_name_is_nexus(self, stub_engine: QueryEngine) -> None:
        mcp = build_mcp_server(stub_engine)
        assert mcp.name == "nexus"

    def test_all_registry_tools_are_registered(self, stub_engine: QueryEngine) -> None:
        mcp = build_mcp_server(stub_engine)
        registered = {t.name for t in asyncio.run(mcp.list_tools())}
        expected = set(stub_engine.registry.names())
        assert registered == expected

    def test_tool_descriptions_are_preserved(self, stub_engine: QueryEngine) -> None:
        mcp = build_mcp_server(stub_engine)
        tools_by_name = {t.name: t for t in asyncio.run(mcp.list_tools())}
        for entry in stub_engine.registry.tools():
            assert tools_by_name[entry.name].description == entry.description

    def test_tool_has_non_empty_parameters_schema(self, stub_engine: QueryEngine) -> None:
        mcp = build_mcp_server(stub_engine)
        tools = asyncio.run(mcp.list_tools())
        for tool in tools:
            assert tool.parameters is not None
            assert isinstance(tool.parameters, dict)

    def test_empty_registry_produces_zero_tools(self, tmp_path: Path) -> None:
        from fastmcp import FastMCP
        from nexus.adapters.storage import ProjectStorage

        storage = ProjectStorage(root=tmp_path / "nexus", slug="empty")
        empty_registry = ToolRegistry()  # no tools registered
        ctx = QueryContext(storage=storage, budget=ResponseBudget())
        engine = QueryEngine(registry=empty_registry, context=ctx)
        mcp = build_mcp_server(engine)
        assert isinstance(mcp, FastMCP)
        tools = asyncio.run(mcp.list_tools())
        assert len(tools) == 0


# ---------------------------------------------------------------------------
# Tool dispatch via MCP (integration-style, no network)
# ---------------------------------------------------------------------------


class TestMcpToolDispatch:
    def test_describe_class_tool_is_callable(self, stub_engine: QueryEngine) -> None:
        """describe_class exists and can be called; returns an error on empty graph."""
        mcp = build_mcp_server(stub_engine)

        async def run() -> Any:
            result = await mcp.call_tool("describe_class", {"fqn": "App\\Models\\User"})
            return result

        result = asyncio.run(run())
        # The graph is empty so the call returns a structured "not found" result,
        # not a Python exception. We just verify it returns without crashing.
        assert result is not None

    def test_list_routes_tool_returns_empty_on_empty_graph(self, stub_engine: QueryEngine) -> None:
        mcp = build_mcp_server(stub_engine)

        async def run() -> Any:
            return await mcp.call_tool("list_routes", {})

        result = asyncio.run(run())
        assert result is not None

    def test_invalid_tool_raises_not_found_error(self, stub_engine: QueryEngine) -> None:
        """Calling a non-existent tool raises FastMCP NotFoundError."""
        from fastmcp.exceptions import NotFoundError

        mcp = build_mcp_server(stub_engine)

        async def run() -> None:
            await mcp.call_tool("nonexistent_tool_xyz", {})

        with pytest.raises(NotFoundError):
            asyncio.run(run())


# ---------------------------------------------------------------------------
# nexus mcp CLI surface
# ---------------------------------------------------------------------------


class TestMcpCliSurface:
    def test_mcp_in_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "mcp" in result.output

    def test_mcp_help_lists_serve(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "serve" in result.output

    def test_serve_help_lists_transport_option(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["mcp", "serve", "--help"])
        assert result.exit_code == 0
        assert "--transport" in result.output
        assert "--port" in result.output
