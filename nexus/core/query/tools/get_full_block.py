"""``get_full_block`` - read a line range out of an indexed source file.

The canonical "escape hatch" when ``semantic_search`` doesn't surface
a known chunk. Given a file and a line range, returns the raw text
that lives there. Used by agents that already know *where* to look
(typically from a prior ``describe_class`` call which reports each
method's line) but cannot retrieve the body any other way.

Design notes
============

* **Path containment.** When ``coverage.project_path`` is available
  the resolved file must live inside the project tree. This is a
  hard gate against agents using the MCP server to read arbitrary
  files (``/etc/passwd`` etc.) on the host. Symlinks are followed
  by ``Path.resolve()`` so a link pointing outside the project
  is also rejected.
* **Relative paths** are joined against ``coverage.project_path``
  when known. Absolute paths are taken as-is.
* **End-line clamping.** A range whose ``end_line`` exceeds the
  file's length is clamped to EOF and the response carries
  ``truncated_to_eof=True`` so an agent knows the underlying file
  was shorter than expected.
* **Errors are values.** The tool never raises for "file missing"
  or "range invalid" - those are documented response shapes with
  a stable ``error_code``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.query.tool_protocol import ToolInput, ToolOutput

if TYPE_CHECKING:
    from nexus.core.query.context import QueryContext


# Upper bound on the ``context_lines`` knob. 20 is enough to surface
# a surrounding method signature and the closing brace without
# bleeding into unrelated code.
MAX_CONTEXT_LINES = 20


class GetFullBlockInput(ToolInput):
    """Identify a file + line range to return verbatim."""

    file_path: str = Field(
        min_length=1,
        description=(
            "Absolute path to the source file, or a path relative to the "
            "indexed project root. Relative paths require the project to "
            "have been indexed (so the engine knows the root)."
        ),
    )
    start_line: int = Field(
        ge=1,
        description="1-indexed inclusive start line.",
    )
    end_line: int = Field(
        ge=1,
        description=(
            "1-indexed inclusive end line. Must be >= ``start_line``. "
            "Values past EOF are clamped and ``truncated_to_eof`` is set."
        ),
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=MAX_CONTEXT_LINES,
        description=(
            "Number of additional lines to include on either side of "
            "the requested range, capped at 20. Useful when an agent "
            "wants the surrounding signature/brace without doing the "
            "math itself."
        ),
    )


class GetFullBlockOutput(ToolOutput):
    """Content of a file range, or a structured error."""

    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    line_count: int = 0
    total_file_lines: int = 0
    content: str | None = None
    truncated_to_eof: bool = Field(
        default=False,
        description=(
            "``True`` when the requested ``end_line`` exceeded the file "
            "length and was clamped to EOF. Agents should treat this as "
            "a signal that their source-of-truth for the line range "
            "(typically ``describe_class``) is stale relative to the "
            "file on disk."
        ),
    )
    file_mtime_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp of the file's on-disk modification "
            "time at read time. ``None`` when the file couldn't be "
            "stat'd or no content was returned (error paths)."
        ),
    )
    chunk_may_be_stale: bool = Field(
        default=False,
        description=(
            "``True`` when ``file_mtime_utc`` is strictly later than "
            "the project's ``indexed_at`` - the file was edited after "
            "the index was built, so the stored ``start_line`` / "
            "``end_line`` (originally taken from ``describe_class`` / "
            "the chunk index) may now point at the wrong region. The "
            "``content`` field still reflects what's currently at "
            "those lines, but the bytes may belong to a different "
            "method now. Re-run ``nexus index sync`` to clear the "
            "signal. ``False`` does NOT positively mean fresh - it "
            "means we have no evidence of staleness (typically "
            "because no ``indexed_at`` is available on the project)."
        ),
    )
    error: str | None = None
    error_code: str | None = None


class GetFullBlockTool:
    """Return the verbatim content of a file's line range."""

    name: ClassVar[str] = "get_full_block"
    description: ClassVar[str] = (
        "Return the raw source text of a file at a given line range. "
        "**Arguments:** ``file_path`` (string, project-relative path "
        'like ``file_path="src/Models/User.php"``), ``start_line`` and '
        "``end_line`` (1-indexed, inclusive integers). "
        "**Optional:** ``context_lines`` (int, default 0) - extra lines "
        "above/below the range. "
        "Use this when ``describe_class`` or another tool tells you "
        "*where* a method/class lives but you need the *body* - e.g., "
        "to read a long ``rules()`` method that ``semantic_search`` "
        "couldn't surface. If you already have a graph node id, prefer "
        "``get_node_body`` which resolves the line range automatically. "
        "Path containment is enforced against the indexed project root."
    )
    input_model: ClassVar[type[ToolInput]] = GetFullBlockInput
    output_model: ClassVar[type[ToolOutput]] = GetFullBlockOutput
    # A single file read off local disk. The hot path is dominated by
    # filesystem latency, not Python work.
    latency_budget_ms: ClassVar[int] = 100

    def execute(  # noqa: PLR0911 - each return is a documented structured-error path
        self,
        payload: GetFullBlockInput,
        ctx: QueryContext,
    ) -> GetFullBlockOutput:
        """Resolve, validate, read, slice."""
        if payload.end_line < payload.start_line:
            return GetFullBlockOutput(
                error=(
                    f"end_line ({payload.end_line}) must be >= start_line ({payload.start_line})."
                ),
                error_code="invalid_range",
            )

        project_root = _project_root(ctx)
        resolved = _resolve_path(payload.file_path, project_root)
        if resolved is None:
            return GetFullBlockOutput(
                error=f"File not found: {payload.file_path!r}.",
                error_code="file_not_found",
            )

        if project_root is not None and not _is_within(resolved, project_root):
            return GetFullBlockOutput(
                error=(
                    f"File {resolved} is outside the indexed project root "
                    f"{project_root}; refusing to read."
                ),
                error_code="file_outside_project",
            )

        if not resolved.is_file():
            return GetFullBlockOutput(
                error=f"Not a regular file: {resolved}.",
                error_code="file_not_found",
            )

        try:
            text = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return GetFullBlockOutput(
                error=f"Could not read {resolved}: {e}.",
                error_code="read_error",
            )

        lines = text.splitlines()
        total = len(lines)

        widened_start = max(1, payload.start_line - payload.context_lines)
        if widened_start > total:
            return GetFullBlockOutput(
                file=str(resolved),
                total_file_lines=total,
                error=(f"start_line ({payload.start_line}) is past EOF ({total} lines)."),
                error_code="range_out_of_bounds",
            )

        widened_end_unclamped = payload.end_line + payload.context_lines
        widened_end = min(total, widened_end_unclamped)
        truncated = widened_end_unclamped > total

        span = lines[widened_start - 1 : widened_end]
        mtime_utc = _read_mtime(resolved)
        return GetFullBlockOutput(
            file=str(resolved),
            start_line=widened_start,
            end_line=widened_end,
            line_count=len(span),
            total_file_lines=total,
            content="\n".join(span),
            truncated_to_eof=truncated,
            file_mtime_utc=mtime_utc,
            chunk_may_be_stale=_chunk_is_stale(mtime_utc, ctx),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root(ctx: QueryContext) -> Path | None:
    """Pull the indexed project root from coverage metadata, if present."""
    if ctx.coverage is None or ctx.coverage.project_path is None:
        return None
    return Path(ctx.coverage.project_path).resolve()


def _resolve_path(raw: str, project_root: Path | None) -> Path | None:
    """Resolve a possibly-relative path to an absolute, symlink-resolved path.

    Returns ``None`` when the path cannot be made absolute (relative
    path with no project root) or when resolution fails (file missing).
    """
    candidate = Path(raw)
    if not candidate.is_absolute():
        if project_root is None:
            return None
        candidate = project_root / candidate

    try:
        return candidate.resolve(strict=True)
    except (OSError, FileNotFoundError):
        return None


def _is_within(path: Path, root: Path) -> bool:
    """``True`` when ``path`` lives inside ``root``.

    Both arguments must already be resolved (no symlinks, no ``..``).
    Uses :meth:`Path.is_relative_to` rather than string prefix matching
    so ``/tmp/foo`` is not treated as a child of ``/tmp/foo-other``.
    """
    return path == root or path.is_relative_to(root)


def _read_mtime(path: Path) -> str | None:
    """Return the file's UTC mtime as an ISO-8601 string, or ``None``."""
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _chunk_is_stale(file_mtime_utc: str | None, ctx: QueryContext) -> bool:
    """``True`` only when both timestamps are present and file > index.

    ``False`` is a "no-evidence-of-staleness" signal rather than a
    positive "is fresh" claim - agents that need stronger guarantees
    should compare ``file_mtime_utc`` against ``coverage.indexed_at``
    themselves and apply whatever tolerance their workflow demands.

    Timestamps are parsed to ``datetime`` for the comparison rather
    than compared lexicographically, so mixed-precision ISO strings
    (e.g. one with microseconds, one without) sort correctly.
    """
    if file_mtime_utc is None:
        return False
    if ctx.coverage is None or ctx.coverage.indexed_at is None:
        return False
    try:
        file_dt = datetime.fromisoformat(file_mtime_utc)
        index_dt = datetime.fromisoformat(ctx.coverage.indexed_at)
    except ValueError:
        return False
    return file_dt > index_dt
