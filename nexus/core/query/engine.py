"""QueryEngine façade — the public entry point for Phase 4.

``QueryEngine.query(name, payload)`` is what the CLI and MCP
server call. It does four things in sequence:

1. Look up the tool in the registry (raise
   :class:`ToolNotFoundError` if missing).
2. Validate the caller's payload against the tool's Pydantic
   input model (raise :class:`ToolInputError` on failure).
3. Construct a fresh tool instance and call ``execute``. Measure
   wall time.
4. Pass the output through the :class:`ResponseBudget` and
   return the trimmed result.

Everything else — the classifier, the auto-generated CLI/MCP
surface — wraps this single entry point.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from nexus.core.query.errors import ToolInputError, ToolNotFoundError
from nexus.core.query.trace import (
    NullQueryTrace,
    record_tool_executed,
)
from nexus.logging import get_logger

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext
    from nexus.core.query.registry import ToolRegistry
    from nexus.core.query.tool_protocol import ToolOutput
    from nexus.core.query.trace import QueryTrace


log = get_logger(__name__)


class QueryEngine:
    """Thin dispatcher over a :class:`ToolRegistry`."""

    def __init__(
        self,
        registry: ToolRegistry,
        context: QueryContext,
        *,
        trace: QueryTrace | None = None,
    ) -> None:
        """Build an engine bound to a registry and a per-run context.

        ``trace`` is optional. When supplied, every ``query()`` call
        emits a ``tool_executed`` record. Pass :class:`NullQueryTrace`
        (or omit) to disable tracing — the engine still records its
        structlog event either way.
        """
        self._registry = registry
        self._context = context
        self._trace: QueryTrace = trace if trace is not None else NullQueryTrace()

    @property
    def trace(self) -> QueryTrace:
        """The active trace sink (a :class:`NullQueryTrace` when disabled)."""
        return self._trace

    def set_trace(self, trace: QueryTrace | None) -> None:
        """Swap the trace sink. ``None`` resets to :class:`NullQueryTrace`."""
        self._trace = trace if trace is not None else NullQueryTrace()

    @property
    def registry(self) -> ToolRegistry:
        """The registry the engine dispatches against."""
        return self._registry

    @property
    def context(self) -> QueryContext:
        """The query-time context threaded through every tool."""
        return self._context

    def query(self, name: str, payload: dict[str, Any] | None = None) -> ToolOutput:
        """Run a tool by name with a dict payload and return its output.

        Args:
            name: Registered tool name.
            payload: Dict of tool-specific arguments. ``None`` is
                treated as ``{}``.

        Returns:
            The validated and budget-trimmed output of the tool.

        Raises:
            ToolNotFoundError: no tool with that name is registered.
            ToolInputError: payload failed Pydantic validation.
        """
        entry = self._registry.get(name)
        if entry is None:
            available = ", ".join(self._registry.names()) or "<none>"
            raise ToolNotFoundError(
                f"Unknown tool {name!r}. Available: {available}",
            )

        tool_class = entry.tool_class
        try:
            validated = tool_class.input_model.model_validate(payload or {})
        except ValidationError as e:
            raise ToolInputError(
                f"Invalid input for tool {name!r}: {e}",
            ) from e

        tool = tool_class()
        start = time.perf_counter()
        output: ToolOutput = tool.execute(validated, self._context)
        duration_ms = (time.perf_counter() - start) * 1000.0

        budget_ms = tool_class.latency_budget_ms
        over_budget = duration_ms > budget_ms
        if over_budget:
            log.warning(
                "tool_over_budget",
                tool=name,
                duration_ms=round(duration_ms, 2),
                budget_ms=budget_ms,
            )
        else:
            log.debug(
                "tool_executed",
                tool=name,
                duration_ms=round(duration_ms, 2),
            )

        trimmed = self._context.budget.trim(output)
        # Attach the index-level coverage block so agents can tell
        # "no matches" from "feature not indexed". Tools leave the
        # field as ``None``; the engine populates it uniformly.
        if self._context.coverage is not None:
            trimmed = trimmed.model_copy(update={"coverage": self._context.coverage})

        # Trace every dispatch.  The null-trace path below short-circuits
        # to a no-op so the cost is one virtual call when tracing is off.
        record_tool_executed(
            self._trace,
            tool=name,
            args=dict(payload or {}),
            duration_ms=duration_ms,
            error_code=getattr(trimmed, "error_code", None),
            result_size=_estimate_result_size(trimmed),
            over_budget=over_budget,
            budget_ms=budget_ms,
        )
        return trimmed


def _estimate_result_size(output: ToolOutput) -> int | None:
    """Return a rough size signal for the trace record.

    For most tools, the meaningful number is the length of the
    primary list field (``routes``, ``hits``, ``rows``, etc.). We
    pick the first list-shaped attribute on the output and report
    its length. ``None`` when the output exposes no list — the
    record reader treats that as "not applicable".
    """
    for field in output.__class__.model_fields:
        value = getattr(output, field, None)
        if isinstance(value, list):
            return len(value)
    return None
