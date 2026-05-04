r"""``find_cache_users`` — reverse lookup from cache key to caller methods.

The static analyser turns each ``Cache::get('foo')`` /
``Cache::put('foo', …)`` call into a ``cache_key:foo`` node and a
``CACHE_READ`` / ``CACHE_WRITE`` edge from the calling method.
This tool walks those edges backwards: given a key (or a glob),
list every method that touches it.

Use this to answer "what reads ``feature.flags``?" or "what
methods invalidate ``user.settings.*``?" without grepping the
codebase.

Match strategy
==============

* If ``key`` matches a ``cache_key`` node exactly, that node alone
  is queried. (Most common case for literal keys.)
* If ``key`` contains a glob character (``*`` or ``?``), every
  ``cache_key`` node whose name matches the glob is queried.
* Otherwise — substring match against every ``cache_key`` node's
  name. Lets the agent ask "anything caching `feature.flags`"
  without typing the surrounding namespace.
"""

from __future__ import annotations

import fnmatch
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import int_attr, str_attr
from nexus.core.query.traversal import incoming

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.query.context import QueryContext


_VALID_MODES: frozenset[str] = frozenset({"any", "read", "write"})


class FindCacheUsersInput(ToolInput):
    """Identify the cache key (or pattern) and which kind of access to look for."""

    key: str = Field(
        min_length=1,
        description=(
            "Cache key to search for. Exact match if the key resolves "
            "to a graph node; otherwise glob (``user.*.session``) or "
            "substring match (``flags``). Always case-sensitive — "
            "Laravel cache keys are."
        ),
    )
    mode: str = Field(
        default="any",
        description=(
            "``read`` for reader methods only, ``write`` for writers "
            "only, ``any`` (default) for both."
        ),
    )


class CacheUsageRow(ToolOutput):
    """One method site that reads or writes the matched key."""

    key: str = Field(description="The matched cache key (resolved to its actual name).")
    mode: str = Field(description="``read`` or ``write`` for this site.")
    class_fqn: str | None = None
    method: str
    file: str | None = None
    line: int | None = None
    form: str | None = Field(
        default=None,
        description="``literal`` or ``prefix`` — see ``CacheKeyUsage`` for context.",
    )


class FindCacheUsersOutput(ToolOutput):
    """Container for the cache-user response."""

    key: str | None = None
    mode: str | None = None
    matched_keys: list[str] = Field(
        default_factory=list,
        description=(
            "The actual cache-key node names this query resolved to. "
            "Helpful when ``key`` was a glob or substring match."
        ),
    )
    total: int = 0
    returned: int = 0
    rows: list[CacheUsageRow] = Field(default_factory=list)
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("rows",)


class FindCacheUsersTool:
    """Reverse-walk ``CACHE_READ`` / ``CACHE_WRITE`` edges from a key."""

    name: ClassVar[str] = "find_cache_users"
    description: ClassVar[str] = (
        "Find every method that reads or writes a cache key. Pass an "
        "exact key (``settings.timezone``), a glob "
        "(``user.*.session``), or a substring (``flags``) and the "
        "tool walks ``CACHE_READ`` / ``CACHE_WRITE`` edges backwards "
        "to surface the call sites with file + line. The ``mode`` "
        "parameter restricts to readers or writers; defaults to both. "
        "Pair with ``describe_class``'s ``cache_keys`` field for the "
        "forward direction (which keys does *this* class touch?)."
    )
    input_model: ClassVar[type[ToolInput]] = FindCacheUsersInput
    output_model: ClassVar[type[ToolOutput]] = FindCacheUsersOutput
    latency_budget_ms: ClassVar[int] = 250

    def execute(
        self,
        payload: FindCacheUsersInput,
        ctx: QueryContext,
    ) -> FindCacheUsersOutput:
        """Resolve the key (or pattern) to nodes and walk back to callers."""
        mode = payload.mode.strip().lower()
        if mode not in _VALID_MODES:
            return FindCacheUsersOutput(
                key=payload.key,
                mode=payload.mode,
                error=(
                    f"Unknown mode {payload.mode!r}. Valid values: "
                    f"{', '.join(sorted(_VALID_MODES))}."
                ),
                error_code="invalid_mode",
            )

        graph = ctx.storage.graph().load()
        cache_nodes = _resolve_cache_keys(graph, payload.key)
        if not cache_nodes:
            return FindCacheUsersOutput(
                key=payload.key,
                mode=mode,
                error=(
                    f"No cache key found matching {payload.key!r}. "
                    f"Check that the index was built with the static "
                    f"analyser (Phase 3) — ``response.coverage.cache_indexed`` "
                    f"is the canary."
                ),
                error_code="key_not_found",
            )

        rows = _collect_users(graph, cache_nodes, mode=mode)
        rows.sort(key=lambda r: (r.class_fqn or "", r.method, r.line or 0))

        return FindCacheUsersOutput(
            key=payload.key,
            mode=mode,
            matched_keys=sorted({n.name for n in cache_nodes}),
            total=len(rows),
            returned=len(rows),
            rows=rows,
        )


# ---------------------------------------------------------------------------
# Resolution + collection helpers
# ---------------------------------------------------------------------------


def _resolve_cache_keys(graph: Graph, query: str) -> list[Node]:
    """Resolve ``query`` to one or more ``cache_key`` nodes.

    Cascade: exact id → glob (when wildcards present) → substring.
    """
    exact_id = f"cache_key:{query}"
    exact_node = graph.node_by_id(exact_id)
    if exact_node is not None:
        return [exact_node]

    has_wildcard = any(ch in query for ch in "*?[")
    matches: list[Node] = []
    for node in graph.nodes:
        if node.kind != NodeKind.CACHE_KEY:
            continue
        if has_wildcard:
            if fnmatch.fnmatchcase(node.name, query):
                matches.append(node)
        elif query in node.name:
            matches.append(node)
    return matches


def _collect_users(
    graph: Graph,
    cache_nodes: list[Node],
    *,
    mode: str,
) -> list[CacheUsageRow]:
    """Walk back from each cache node along READ/WRITE edges."""
    rows: list[CacheUsageRow] = []
    edge_kinds: tuple[EdgeKind, ...]
    if mode == "read":
        edge_kinds = (EdgeKind.CACHE_READ,)
    elif mode == "write":
        edge_kinds = (EdgeKind.CACHE_WRITE,)
    else:
        edge_kinds = (EdgeKind.CACHE_READ, EdgeKind.CACHE_WRITE)

    for cache_node in cache_nodes:
        for edge_kind in edge_kinds:
            row_mode = "read" if edge_kind == EdgeKind.CACHE_READ else "write"
            for edge in incoming(graph, cache_node.id, edge_kind):
                source = graph.node_by_id(edge.source)
                if source is None:
                    continue
                edge_attrs = dict(edge.attributes)
                rows.append(
                    CacheUsageRow(
                        key=cache_node.name,
                        mode=row_mode,
                        class_fqn=str_attr(source.attributes, "class_fqn"),
                        method=source.name,
                        file=str_attr(edge_attrs, "file") or str_attr(source.attributes, "file"),
                        line=int_attr(edge_attrs, "line") or int_attr(source.attributes, "line"),
                        form=str_attr(edge_attrs, "form"),
                    ),
                )
    return rows
