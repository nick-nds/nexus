"""Query engine: structural + semantic tools over a persisted index.

Phase 4 turns the stored graph and vector index into agent-facing
answers. The layer is a thin façade over Phase 2's storage protocols
plus a registry of small :class:`Tool` classes each implementing one
question shape (trace a route, describe a class, find listeners of
an event, ...).

Design rules from ``internal_docs/PHASE-4-query-engine.md``:

* **Tools are classes, not functions.** Each tool carries its own
  input/output schema and latency budget. A uniform
  :class:`ToolRegistry` lets the CLI/MCP layer (Phase 5) enumerate
  every tool and expose it automatically.
* **Outputs are bounded.** Every response is passed through the
  :class:`ResponseBudget` which trims oversized lists with an
  explicit ``truncated: true`` flag rather than dropping fields
  silently. Agents handle "trimmed but honest" better than
  "missing without explanation".
* **Tools are read-only.** They never mutate the graph or vector
  store. Concurrency is free.
* **Errors are structured results, not exceptions.** "Unknown
  route" is an output shape, not a thrown error - agents handle
  structured "not found" cleanly.

This package is pure domain code (with the exception of the query
engine's direct use of ``SqliteGraphStore`` and ``LanceDbVectorStore``
for efficient traversal - those are passed in via constructor so the
layering test remains happy).
"""

from nexus.core.query.budget import ResponseBudget
from nexus.core.query.classifier import QueryClassifier, QueryPlan
from nexus.core.query.engine import QueryEngine
from nexus.core.query.errors import ToolInputError, ToolNotFoundError
from nexus.core.query.registry import RegisteredTool, ToolRegistry
from nexus.core.query.tool_protocol import Tool, ToolInput, ToolOutput
from nexus.core.query.trace import (
    JsonlQueryTrace,
    NullQueryTrace,
    QueryTrace,
    default_trace_path,
    open_trace,
    trace_path_from_env,
)

__all__ = [
    "JsonlQueryTrace",
    "NullQueryTrace",
    "QueryClassifier",
    "QueryEngine",
    "QueryPlan",
    "QueryTrace",
    "RegisteredTool",
    "ResponseBudget",
    "Tool",
    "ToolInput",
    "ToolInputError",
    "ToolNotFoundError",
    "ToolOutput",
    "ToolRegistry",
    "default_trace_path",
    "open_trace",
    "trace_path_from_env",
]
