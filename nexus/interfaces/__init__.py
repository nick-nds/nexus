"""Interface-layer adapters.

Phase 5 adapters that wrap the pure-core query engine and pipeline in
user-facing surfaces:

* :mod:`nexus.interfaces.cli` - Click-based command-line interface.
* :mod:`nexus.interfaces.mcp` - FastMCP server exposing every tool as
  an MCP endpoint.

Both adapters iterate :class:`nexus.core.query.ToolRegistry` to
auto-generate their public surface so adding a new tool means writing
one class in :mod:`nexus.core.query.tools` and getting both the CLI
subcommand and the MCP endpoint for free.
"""
