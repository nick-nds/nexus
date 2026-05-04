"""FastMCP server adapter for Nexus.

Exposes every registered :class:`~nexus.core.query.registry.RegisteredTool`
as an MCP tool with the same name, description, and input schema. The
handler is a thin shim that calls :meth:`~nexus.core.query.engine.QueryEngine.execute`
and serialises the output.

Usage::

    from nexus.interfaces.mcp import build_mcp_server
    from nexus.core.query import QueryEngine
    mcp = build_mcp_server(engine)
    mcp.run()  # stdio by default
"""

from nexus.interfaces.mcp.server import build_mcp_server

__all__ = ["build_mcp_server"]
