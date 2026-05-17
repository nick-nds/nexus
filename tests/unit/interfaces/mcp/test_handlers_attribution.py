"""MCP responses append an attribution 'package' field for package-kind projects.

Tests verify the _dispatch closure in the MCP server builder injects
the attribution block when the project's meta.kind == "package", and
leaves project-kind responses unchanged.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nexus.adapters.storage.project_storage import ProjectMeta
from nexus.core.query import QueryEngine, ResponseBudget, ToolOutput, ToolRegistry
from nexus.core.query.context import QueryContext
from nexus.core.query.tools import register_builtin_tools
from nexus.core.reflection.document import PackageMetadata
from nexus.interfaces.mcp import build_mcp_server
from pydantic import Field

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _StubOutput(ToolOutput):
    total: int = 7
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)


def _package_meta() -> ProjectMeta:
    return ProjectMeta(
        project_slug="vendor--pkg",
        project_path="/x",
        kind="package",
        package=PackageMetadata(
            vendor="vendor",
            name="pkg",
            version="2.0",
            license="Apache-2.0",
        ),
    )


def _project_meta() -> ProjectMeta:
    return ProjectMeta(project_slug="my-app", project_path="/x")


def _build_engine(tmp_path: Path, meta: ProjectMeta | None = None) -> QueryEngine:
    """Build a real engine backed by a real ProjectStorage with optional meta."""
    from nexus.adapters.storage import ProjectStorage

    storage = ProjectStorage(root=tmp_path / "nexus", slug="test")
    if meta is not None:
        storage.initialise()
        storage.write_meta(meta)

    registry = ToolRegistry()
    register_builtin_tools(registry)
    ctx = QueryContext(storage=storage, budget=ResponseBudget())
    return QueryEngine(registry=registry, context=ctx)


# ---------------------------------------------------------------------------
# Package-kind: attribution present
# ---------------------------------------------------------------------------


def _call_tool(mcp: Any, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool and return its structured_content dict.

    FastMCP's ``call_tool`` returns a ``ToolResult`` whose
    ``structured_content`` attribute is the raw dict our ``_dispatch``
    returned. Using it avoids JSON parsing and is more direct.
    """
    result = asyncio.run(mcp.call_tool(tool_name, args))
    assert result.structured_content is not None, "MCP tool returned no structured content"
    return result.structured_content  # type: ignore[return-value]


class TestMcpAttributionPackageKind:
    def test_package_field_in_mcp_response(self, tmp_path: Path) -> None:
        engine = _build_engine(tmp_path, meta=_package_meta())
        mcp = build_mcp_server(engine)

        data = _call_tool(mcp, "list_routes", {})
        assert "package" in data
        assert data["package"]["vendor"] == "vendor"
        assert data["package"]["name"] == "pkg"
        assert data["package"]["version"] == "2.0"
        assert data["package"]["license"] == "Apache-2.0"

    def test_tool_result_fields_preserved(self, tmp_path: Path) -> None:
        engine = _build_engine(tmp_path, meta=_package_meta())
        mcp = build_mcp_server(engine)

        data = _call_tool(mcp, "list_routes", {})
        # list_routes output always has "routes" and "truncated"
        assert "routes" in data
        assert "truncated" in data


# ---------------------------------------------------------------------------
# Project-kind: no attribution
# ---------------------------------------------------------------------------


class TestMcpAttributionProjectKind:
    def test_no_package_field_for_project_kind(self, tmp_path: Path) -> None:
        engine = _build_engine(tmp_path, meta=_project_meta())
        mcp = build_mcp_server(engine)

        data = _call_tool(mcp, "list_routes", {})
        assert "package" not in data

    def test_no_package_field_when_no_meta_file(self, tmp_path: Path) -> None:
        # No meta written — storage.read_meta() returns None
        engine = _build_engine(tmp_path, meta=None)
        mcp = build_mcp_server(engine)

        data = _call_tool(mcp, "list_routes", {})
        assert "package" not in data
