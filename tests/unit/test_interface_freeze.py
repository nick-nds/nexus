"""Backward-compatibility freeze tests for the CLI and MCP interfaces.

Design reference: ``internal_docs/PHASE-5-interface-layer.md`` §D5.10.

These tests freeze the public contract of Nexus v1.0:

* The names of every CLI command/subcommand.
* The names of every MCP tool exposed via the registry.

**What to do when these tests fail:**

A test failure here means you changed a public name. That's a
breaking change that requires:

1. A decision log entry in ``internal_docs/13-decision-log.md``.
2. A major version bump (or a deliberate rename during the v1.0
   stabilisation window).
3. An update to the expected set below.

Do NOT just update the set to make CI green without the decision log
entry — the whole point of this test is to make accidental renames
loud.
"""

from __future__ import annotations

import asyncio

import pytest
from nexus.core.query import QueryEngine, ResponseBudget, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.interfaces.mcp import build_mcp_server

# ---------------------------------------------------------------------------
# Expected public contracts (frozen at v1.0)
# ---------------------------------------------------------------------------

#: All top-level ``nexus`` subcommands.
EXPECTED_CLI_COMMANDS = frozenset(
    {
        "ask",
        "cache",
        "doctor",
        "index",
        "init",
        "install-hooks",
        "mcp",
        "package",
        "profile",
        "query",
        "trace",
    }
)

#: All ``nexus index`` subcommands.
EXPECTED_INDEX_SUBCOMMANDS = frozenset({"rebuild", "sync", "status", "clear"})

#: All ``nexus query`` subcommands (= registered OSS tool names).
EXPECTED_QUERY_SUBCOMMANDS = frozenset(
    {
        "describe_class",
        "describe_flow",
        "describe_module",
        "expand_call_tree",
        "explore_entity",
        "find_cache_users",
        "find_callers",
        "find_dispatchers",
        "find_event_chains",
        "find_handlers",
        "find_implementations",
        "find_jobs_dispatching",
        "find_listeners",
        "get_full_block",
        "get_model_context",
        "get_node_body",
        "get_policy_for",
        "get_request_flow",
        "list_by_kind",
        "list_modules",
        "list_routes",
        "list_scheduled_tasks",
        "resolve_binding",
        "semantic_search",
        "trace_route",
    }
)

#: All ``nexus profile`` subcommands.
EXPECTED_PROFILE_SUBCOMMANDS = frozenset({"detect", "list", "show"})

#: All ``nexus cache`` subcommands.
EXPECTED_CACHE_SUBCOMMANDS = frozenset({"clear", "size"})

#: All ``nexus mcp`` subcommands.
EXPECTED_MCP_SUBCOMMANDS = frozenset({"serve"})

#: All ``nexus trace`` subcommands.
EXPECTED_TRACE_SUBCOMMANDS = frozenset({"inspect"})

#: MCP tool names (must match EXPECTED_QUERY_SUBCOMMANDS 1:1).
EXPECTED_MCP_TOOL_NAMES = EXPECTED_QUERY_SUBCOMMANDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cli_commands() -> frozenset[str]:
    """Return the set of top-level CLI command names."""
    from click.testing import CliRunner
    from nexus.interfaces.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    # Parse the "Commands:" section — lines that start with exactly two spaces
    # followed by a non-whitespace command name.
    commands: set[str] = set()
    in_commands = False
    for line in result.output.splitlines():
        if line.strip().startswith("Commands:"):
            in_commands = True
            continue
        if in_commands:
            stripped = line.strip()
            if not stripped:
                break
            commands.add(stripped.split()[0])
    return frozenset(commands)


def _subcommands_of(group: str) -> frozenset[str]:
    """Return the subcommand names listed in ``nexus <group> --help``."""
    from click.testing import CliRunner
    from nexus.interfaces.cli.main import main

    runner = CliRunner()
    result = runner.invoke(main, [group, "--help"])
    assert result.exit_code == 0
    commands: set[str] = set()
    in_commands = False
    for line in result.output.splitlines():
        if line.strip().startswith("Commands:"):
            in_commands = True
            continue
        if in_commands:
            stripped = line.strip()
            if not stripped:
                break
            commands.add(stripped.split()[0])
    return frozenset(commands)


@pytest.fixture(scope="module")
def mcp_tool_names(tmp_path_factory: pytest.TempPathFactory) -> frozenset[str]:
    """Build the MCP server and return its registered tool names."""
    tmp = tmp_path_factory.mktemp("mcp_snap")
    from nexus.adapters.storage import ProjectStorage

    storage = ProjectStorage(root=tmp / "nexus", slug="snap")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    engine = QueryEngine(registry=registry, context=ctx)
    mcp = build_mcp_server(engine)
    tools = asyncio.run(mcp.list_tools())
    return frozenset(t.name for t in tools)


# ---------------------------------------------------------------------------
# CLI freeze tests
# ---------------------------------------------------------------------------


class TestCliFreezeCommands:
    def test_top_level_commands_are_frozen(self) -> None:
        actual = _cli_commands()
        added = actual - EXPECTED_CLI_COMMANDS
        removed = EXPECTED_CLI_COMMANDS - actual
        assert not added, f"New CLI commands (update freeze set): {added}"
        assert not removed, f"Removed CLI commands (breaking change): {removed}"

    def test_index_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("index")
        assert actual == EXPECTED_INDEX_SUBCOMMANDS

    def test_query_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("query")
        assert actual == EXPECTED_QUERY_SUBCOMMANDS

    def test_profile_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("profile")
        assert actual == EXPECTED_PROFILE_SUBCOMMANDS

    def test_cache_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("cache")
        assert actual == EXPECTED_CACHE_SUBCOMMANDS

    def test_mcp_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("mcp")
        assert actual == EXPECTED_MCP_SUBCOMMANDS

    def test_trace_subcommands_are_frozen(self) -> None:
        actual = _subcommands_of("trace")
        assert actual == EXPECTED_TRACE_SUBCOMMANDS


# ---------------------------------------------------------------------------
# MCP freeze tests
# ---------------------------------------------------------------------------


class TestMcpFreezeToolNames:
    def test_mcp_tool_names_are_frozen(self, mcp_tool_names: frozenset[str]) -> None:
        added = mcp_tool_names - EXPECTED_MCP_TOOL_NAMES
        removed = EXPECTED_MCP_TOOL_NAMES - mcp_tool_names
        assert not added, f"New MCP tools (update freeze set): {added}"
        assert not removed, f"Removed MCP tools (breaking change): {removed}"

    def test_mcp_tools_match_query_subcommands(self, mcp_tool_names: frozenset[str]) -> None:
        """MCP tool names and query subcommands must be identical."""
        assert mcp_tool_names == EXPECTED_QUERY_SUBCOMMANDS
