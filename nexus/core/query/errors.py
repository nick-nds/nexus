"""Typed exceptions for the query engine.

Two kinds of error warrant a real Python exception:

* :class:`ToolNotFoundError` — the caller asked for a tool name
  that's not in the registry. That's a caller bug or a stale
  cached tool list; either way, it's not a business "result".
* :class:`ToolInputError` — the caller passed input that doesn't
  validate against the tool's declared input schema. Phase 5's CLI
  and MCP server both parse the schema and construct inputs, so a
  validation failure here is a programmer error, not a user-facing
  condition.

Business-level "not found" results (unknown route, missing class)
are returned as typed output shapes, not exceptions.
"""

from __future__ import annotations


class QueryEngineError(Exception):
    """Base class for query-engine failures."""


class ToolNotFoundError(QueryEngineError):
    """The requested tool name is not registered."""


class ToolInputError(QueryEngineError):
    """The tool's input failed validation."""
