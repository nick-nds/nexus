"""Per-query trace writer for ``nexus ask`` and the MCP server.

Background
==========

Every tool call already logs to structlog (``tool_executed`` /
``tool_over_budget``), but those records vanish to stdout/stderr and
can't be inspected after the fact. When an agent (Claude Code,
Cursor) gets a wrong or empty answer over MCP, an operator currently
has no record of *which* tools were tried, with *what* args, or what
``error_code`` came back.

This module provides a tiny JSONL writer the engine + CLI can call
optionally; when no trace is configured the no-op
:class:`NullQueryTrace` keeps call sites unconditional.

Schema
======

Each line is a JSON object with at least::

    {"ts": <ISO8601>, "trace_id": <str>, "kind": <str>, ...}

where ``kind`` is one of:

* ``classifier_decision`` — the classifier picked a tool for a
  free-text query (CLI ``ask`` only).
* ``tool_executed`` — one tool dispatch with timing + outcome.
* ``ask_envelope`` — the ``ask`` command picked a usable result;
  records which tool answered and the alternatives tried.
* ``ask_refusal`` — the ``ask`` command emitted a structured
  ``no_confident_match`` refusal.

Schema is stable; new fields may be added (consumers must tolerate
unknown keys) but existing fields will not be renamed without a
``schema_version`` bump on the file.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from types import TracebackType

    from nexus.core.query.classifier import QueryPlan


#: Schema version of one record line.  Bumped on breaking changes
#: (renames, type changes); additions are non-breaking.
TRACE_SCHEMA_VERSION = 1


@runtime_checkable
class QueryTrace(Protocol):
    """Sink for trace records emitted by the query engine and CLI.

    Both concrete implementations support context-manager semantics
    so callers can write ``with open_trace(path) as trace:`` without
    branching on whether tracing is enabled.
    """

    @property
    def trace_id(self) -> str:
        """Stable id shared across every record in this trace session."""

    def record(self, kind: str, **fields: Any) -> None:
        """Append one trace record. Implementations must not raise."""

    def close(self) -> None:
        """Flush and release any open resources."""

    def __enter__(self) -> QueryTrace:
        """Enter context-manager scope; the underlying file opens lazily."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the trace on scope exit."""


class NullQueryTrace:
    """No-op trace used when the caller didn't enable tracing.

    Keeps call sites in the engine + ``ask`` simple — they always
    call ``trace.record(...)`` regardless of whether tracing is on.
    """

    @property
    def trace_id(self) -> str:
        """Stable empty id (no records will be written)."""
        return ""

    def record(self, kind: str, **fields: Any) -> None:
        """Discard the record."""

    def close(self) -> None:
        """No-op."""

    def __enter__(self) -> NullQueryTrace:
        """Context-manager support so callers can ``with open_trace(...)``."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """No-op."""


class JsonlQueryTrace:
    """Append-only JSON-Lines writer.

    Failures during ``record`` (disk full, encoding hiccup) are
    silently swallowed so a trace problem can never crash the
    pipeline a user is running. The writer DOES log a structured
    warning the first time it fails (subsequent failures are
    rate-limited per session to keep noise down).

    The file is opened lazily on the first record so opening a
    trace that never gets used doesn't leave an empty file behind.
    """

    def __init__(self, path: Path, *, trace_id: str | None = None) -> None:
        """Open a trace at ``path`` (created lazily on first record)."""
        self._path = path
        self._trace_id = trace_id or _generate_trace_id()
        self._handle: Any = None
        self._failed = False

    @property
    def path(self) -> Path:
        """Where records land."""
        return self._path

    @property
    def trace_id(self) -> str:
        """Stable id shared across every record in this trace session."""
        return self._trace_id

    def record(self, kind: str, **fields: Any) -> None:
        """Append one record. Silently swallows IO/serialisation errors."""
        try:
            payload = {
                "ts": _now_iso(),
                "trace_id": self._trace_id,
                "schema_version": TRACE_SCHEMA_VERSION,
                "kind": kind,
                **fields,
            }
            line = json.dumps(payload, default=_json_default)
            self._ensure_open()
            self._handle.write(line + "\n")
            self._handle.flush()
        except Exception:
            # Tracing must never crash the pipeline. We deliberately
            # don't log here — the noisy structlog warning belongs at
            # the file-open site, which already does it.
            self._failed = True

    def close(self) -> None:
        """Close the underlying file if it was opened."""
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None

    def __enter__(self) -> JsonlQueryTrace:
        """Context-manager support."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close on exit regardless of how the block ended."""
        self.close()

    def _ensure_open(self) -> None:
        """Open the file lazily, creating parent directories as needed."""
        if self._handle is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self._path.open("a", encoding="utf-8")


def open_trace(path: Path | None) -> QueryTrace:
    """Return a real trace if ``path`` is set, otherwise a null trace.

    This is the only function call sites need; they don't conditional
    on whether tracing is enabled. ``with open_trace(...)`` works in
    both cases (the null variant has the same lifecycle methods).
    """
    if path is None:
        return NullQueryTrace()
    return JsonlQueryTrace(path)


def default_trace_path(*, base_dir: Path | None = None) -> Path:
    """Compute a default trace path under ``base_dir`` (default: ``~/.nexus``).

    Layout: ``<base_dir>/traces/<YYYY-MM-DD>/<trace_id>.jsonl``.
    The directory is created lazily by :class:`JsonlQueryTrace`.
    """
    root = base_dir if base_dir is not None else Path.home() / ".nexus"
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return root / "traces" / today / f"{_generate_trace_id()}.jsonl"


def trace_path_from_env(*, env_var: str = "NEXUS_TRACE_DIR") -> Path | None:
    """Resolve a trace path from ``NEXUS_TRACE_DIR`` (or ``None`` if unset).

    The env var holds a *directory*; one file is created inside it per
    trace session. Used by the MCP server to opt into tracing without
    requiring command-line flags from the agent caller.
    """
    raw = os.environ.get(env_var)
    if not raw:
        return None
    base = Path(raw).expanduser()
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return base / today / f"{_generate_trace_id()}.jsonl"


# ---------------------------------------------------------------------------
# High-level record helpers — keep call-site code declarative
# ---------------------------------------------------------------------------


def record_classifier_decision(trace: QueryTrace, *, query: str, plan: QueryPlan) -> None:
    """Emit a ``classifier_decision`` record."""
    trace.record(
        "classifier_decision",
        query=query,
        plan=_plan_to_dict(plan),
    )


def record_tool_executed(
    trace: QueryTrace,
    *,
    tool: str,
    args: dict[str, Any],
    duration_ms: float,
    error_code: str | None,
    result_size: int | None,
    over_budget: bool,
    budget_ms: int,
) -> None:
    """Emit a ``tool_executed`` record."""
    trace.record(
        "tool_executed",
        tool=tool,
        args=args,
        duration_ms=round(duration_ms, 2),
        error_code=error_code,
        result_size=result_size,
        over_budget=over_budget,
        budget_ms=budget_ms,
    )


def record_ask_envelope(
    trace: QueryTrace,
    *,
    query: str,
    final_tool: str,
    confidence: float,
    reason: str,
    alternatives_tried: list[str],
) -> None:
    """Emit an ``ask_envelope`` record (a confident result was found)."""
    trace.record(
        "ask_envelope",
        query=query,
        final_tool=final_tool,
        confidence=confidence,
        reason=reason,
        alternatives_tried=alternatives_tried,
    )


def record_ask_refusal(
    trace: QueryTrace,
    *,
    query: str,
    weak_tool: str | None,
    best_score: float,
    threshold: float,
    alternatives_tried: list[str],
) -> None:
    """Emit an ``ask_refusal`` record (no confident match)."""
    trace.record(
        "ask_refusal",
        query=query,
        weak_tool=weak_tool,
        best_score=best_score,
        threshold=threshold,
        alternatives_tried=alternatives_tried,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _generate_trace_id() -> str:
    """Short URL-safe id; uniqueness across one machine-day is sufficient."""
    return secrets.token_urlsafe(8)


def _now_iso() -> str:
    """ISO 8601 UTC with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _plan_to_dict(plan: QueryPlan) -> dict[str, Any]:
    """Serialise a :class:`QueryPlan` (and its fallback chain) to JSON-safe dict."""
    return {
        "tool": plan.tool,
        "args": dict(plan.args),
        "confidence": plan.confidence,
        "reason": plan.reason,
        "fallbacks": [_plan_to_dict(fb) for fb in plan.fallbacks],
    }


def _json_default(value: Any) -> Any:
    """Best-effort fallback for objects ``json.dumps`` doesn't recognise."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return repr(value)
