"""``get_node_body`` — resolve a graph node id to its source text.

Built on top of :class:`~nexus.core.query.tools.get_full_block.GetFullBlockTool`.
Where ``get_full_block`` takes ``(file, start_line, end_line)``, this
tool takes a single ``node_id`` and resolves the range itself by:

1. Looking up the node in the graph (for kind, name, container class).
2. Finding the chunk in the vector store whose payload references the
   same ``node_id`` (carries the authoritative ``end_line``, since the
   graph stores only ``start_line``).
3. Delegating the actual file read + clamp logic to
   ``GetFullBlockTool``.

The chunk-lookup walks the entire vector store once on first call,
then caches the resulting ``node_id → chunk`` map keyed by the
vector-store instance. Subsequent calls in the same session are O(1)
on the chunk side. The cost of the first call is one full table scan
(~50-100 ms at the helm-v7 scale).

A follow-up issue tracks lifting chunk metadata onto graph nodes at
index time so this scan can be replaced with a direct attribute read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from weakref import WeakKeyDictionary

from pydantic import Field

from nexus.core.graph.types import NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import (
    file_for_method_node,
    int_attr,
    str_attr,
)
from nexus.core.query.tools.get_full_block import (
    MAX_CONTEXT_LINES,
    GetFullBlockInput,
    GetFullBlockTool,
)

if TYPE_CHECKING:
    from typing import Any

    from nexus.core.protocols import VectorStore
    from nexus.core.query.context import QueryContext


# ---------------------------------------------------------------------------
# Chunk locator
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ChunkLocation:
    """The chunk-payload subset we need to render a node body."""

    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    kind: str
    symbol: str | None


# Cache the chunk index per vector-store instance. Stored as a
# :class:`WeakKeyDictionary` so the cache evicts when the project
# storage closes; we don't hold the vector store alive past its
# legitimate lifetime.
_chunk_index_cache: WeakKeyDictionary[Any, dict[str, _ChunkLocation]] = WeakKeyDictionary()


def _chunk_index(store: VectorStore) -> dict[str, _ChunkLocation]:
    """Return ``node_id → _ChunkLocation`` for every chunk in ``store``."""
    cached = _chunk_index_cache.get(store)
    if cached is not None:
        return cached

    index: dict[str, _ChunkLocation] = {}
    for record in store.iter_records():
        payload = record.payload
        node_id_raw = payload.get("node_id")
        if not isinstance(node_id_raw, str) or not node_id_raw:
            continue
        if node_id_raw in index:
            # First-write-wins; the chunker emits at most one chunk per
            # node id in practice. If a future chunker emits multiple
            # (e.g., long methods split into windows) we'll need to
            # surface them as a list — flagged as a TODO at the
            # benchmarking follow-up.
            continue
        file_path = payload.get("file_path")
        start_line = payload.get("start_line")
        end_line = payload.get("end_line")
        if (
            not isinstance(file_path, str)
            or not isinstance(start_line, int)
            or not isinstance(end_line, int)
        ):
            continue
        index[node_id_raw] = _ChunkLocation(
            chunk_id=record.id,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            kind=str(payload.get("kind") or ""),
            symbol=str(payload.get("symbol") or "") or None,
        )

    _chunk_index_cache[store] = index
    return index


# ---------------------------------------------------------------------------
# Tool models
# ---------------------------------------------------------------------------


class GetNodeBodyInput(ToolInput):
    """Identify a graph node whose source text we want."""

    node_id: str = Field(
        min_length=1,
        description=(
            "Graph node id, e.g. "
            "``method:App\\Models\\User::scopeActive`` for a method or "
            "``class:App\\Models\\User`` for a class. Returned by every "
            "other tool that surfaces nodes (``describe_class``, "
            "``semantic_search``, ``find_callers``, …)."
        ),
    )
    context_lines: int = Field(
        default=0,
        ge=0,
        le=MAX_CONTEXT_LINES,
        description=(
            "Number of extra lines to include on either side of the "
            "node's declared range, capped at 20."
        ),
    )


class GetNodeBodyOutput(ToolOutput):
    """Source text for a graph node, or a structured error."""

    node_id: str | None = None
    node_kind: str | None = None
    symbol: str | None = None
    container_class: str | None = Field(
        default=None,
        description=(
            "FQN of the class a method node belongs to. ``None`` for "
            "class-like nodes (no enclosing class) and for nodes "
            "without a source location."
        ),
    )
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    line_count: int = 0
    total_file_lines: int = 0
    content: str | None = None
    truncated_to_eof: bool = False
    file_mtime_utc: str | None = Field(
        default=None,
        description=(
            "ISO-8601 UTC timestamp of the resolved source file's "
            "on-disk modification time at read time. ``None`` on "
            "error paths."
        ),
    )
    chunk_may_be_stale: bool = Field(
        default=False,
        description=(
            "``True`` when ``file_mtime_utc`` is strictly later than "
            "the project's ``indexed_at`` — the file was edited after "
            "the chunk's line range was recorded, so the stored range "
            "may now point at the wrong region of the file. ``content`` "
            "still reflects what's currently at those lines, but the "
            "bytes may no longer belong to this node. ``False`` does "
            "NOT mean fresh — only that we have no evidence of "
            "staleness (typically because no ``indexed_at`` is set)."
        ),
    )
    error: str | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class GetNodeBodyTool:
    """Return the source text of a graph node by id."""

    name: ClassVar[str] = "get_node_body"
    description: ClassVar[str] = (
        "Return the raw source text of a graph node (method or class) "
        "given its ``node_id``. Use this when ``describe_class`` or "
        "``find_callers`` gives you a node id but you still need to "
        "read the actual body — typically because ``semantic_search`` "
        "couldn't surface it. The line range comes from the chunk "
        "metadata, so end-line is accurate even for methods whose "
        "graph node only carries the start line."
    )
    input_model: ClassVar[type[ToolInput]] = GetNodeBodyInput
    output_model: ClassVar[type[ToolOutput]] = GetNodeBodyOutput
    # First call pays a one-time full scan of the vector store to
    # build the node_id->chunk map (measured ~3.5 s at the synthesq-api
    # scale of ~20k chunks). Subsequent calls in the same session are
    # sub-millisecond. The budget is set to the cold-path cost so we
    # don't emit a misleading "over budget" warning on every first call.
    # The follow-up chunker/enrichment benchmarking issue tracks
    # replacing the scan with a direct node-attribute lookup.
    latency_budget_ms: ClassVar[int] = 5000

    def execute(
        self,
        payload: GetNodeBodyInput,
        ctx: QueryContext,
    ) -> GetNodeBodyOutput:
        """Resolve node → chunk → file range → content."""
        graph = ctx.storage.graph().load()
        node = graph.node_by_id(payload.node_id)
        if node is None:
            return GetNodeBodyOutput(
                node_id=payload.node_id,
                error=f"No node found with id {payload.node_id!r}.",
                error_code="node_not_found",
            )

        if node.kind == NodeKind.CONTROLLER_METHOD:
            file_path = file_for_method_node(graph, node)
            container_class = str_attr(node.attributes, "class_fqn")
        else:
            file_path = str_attr(node.attributes, "file")
            container_class = None

        chunk = _lookup_chunk(ctx, payload.node_id)
        if chunk is None:
            # No chunk means we can't determine the end line. If the
            # node also has no file or line at all (routes, bindings),
            # report node_has_no_source; otherwise it's specifically a
            # chunk-not-found case.
            has_any_location = (
                file_path is not None or int_attr(node.attributes, "line") is not None
            )
            return GetNodeBodyOutput(
                node_id=payload.node_id,
                node_kind=node.kind.value,
                symbol=node.name,
                container_class=container_class,
                error=(
                    "No chunk indexed for this node — cannot determine "
                    "end line. Re-run ``nexus index sync`` to ensure "
                    "the chunk is present."
                    if has_any_location
                    else (
                        "This node has no source location (no file/line "
                        "attribute and no chunk). Kind "
                        f"{node.kind.value!r} is not retrievable."
                    )
                ),
                error_code=("chunk_not_found" if has_any_location else "node_has_no_source"),
            )

        # Chunk has the authoritative file too — use it as a fallback.
        resolved_file = file_path or chunk.file_path

        block_out = GetFullBlockTool().execute(
            GetFullBlockInput(
                file_path=resolved_file,
                start_line=chunk.start_line,
                end_line=chunk.end_line,
                context_lines=payload.context_lines,
            ),
            ctx,
        )

        return GetNodeBodyOutput(
            node_id=payload.node_id,
            node_kind=node.kind.value,
            symbol=node.name,
            container_class=container_class,
            file=block_out.file,
            start_line=block_out.start_line,
            end_line=block_out.end_line,
            line_count=block_out.line_count,
            total_file_lines=block_out.total_file_lines,
            content=block_out.content,
            truncated_to_eof=block_out.truncated_to_eof,
            file_mtime_utc=block_out.file_mtime_utc,
            chunk_may_be_stale=block_out.chunk_may_be_stale,
            error=block_out.error,
            error_code=block_out.error_code,
        )


def _lookup_chunk(ctx: QueryContext, node_id: str) -> _ChunkLocation | None:
    """Resolve a node id to its chunk metadata via the cached index."""
    if ctx.vector_dimensions is None:
        return None
    store = ctx.storage.vectors(dimensions=ctx.vector_dimensions)
    return _chunk_index(store).get(node_id)
