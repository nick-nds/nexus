"""``semantic_search`` — vector retrieval with graph-aware re-ranking.

This is the only non-structural tool in the Phase 4 batch. It
answers free-text questions ("where do we send welcome emails?")
by:

1. Embedding the query with the context's embedder.
2. Fetching the top-``top_k`` nearest chunks from LanceDB.
3. Mapping each hit back to its graph node (via
   ``payload.node_id``).
4. Walking one hop around the node to gather small structural
   annotations (containing class, routes pointing at it, etc.).
5. De-duplicating by node id — multiple chunks from the same
   method collapse into one result row.
6. Re-ranking with a small deterministic heuristic: the vector
   similarity score is multiplied by a node-kind weight that
   nudges "interesting" kinds (methods, routes, events) above
   catch-all classes and chunks.
7. Returning the top-``final_k`` rows plus a tiny text preview.

The re-rank heuristic is intentionally simple; see design
decision D4.5 in ``PHASE-4-query-engine.md``. Weights live in
module constants so a future profile can override them without
touching the tool body.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pydantic import Field

from nexus.core.graph.types import EdgeKind, NodeKind
from nexus.core.query.tool_protocol import ToolInput, ToolOutput
from nexus.core.query.tools._common import str_attr, str_list_attr
from nexus.core.query.traversal import incoming, outgoing

if TYPE_CHECKING:
    from nexus.core.graph.graph import Graph
    from nexus.core.graph.types import Node
    from nexus.core.protocols import VectorSearchHit
    from nexus.core.query.context import QueryContext


# Default candidate window retrieved from the vector store before
# re-ranking. Too small and the graph expansion can't recover from
# noisy nearest-neighbour picks; too large and the cosine search
# latency dominates. 30 is the number used in the design doc.
DEFAULT_TOP_K = 30

# Number of results returned to the caller after re-ranking.
DEFAULT_FINAL_K = 10

# Default lines of source rendered into each hit's ``snippet``. Picked
# so an agent gets enough surrounding code to verify relevance without
# re-reading the file, while keeping responses well under the typical
# MCP payload budget for a 10-hit result.
DEFAULT_SNIPPET_LINES = 30

# Hard upper bound on snippet length. The cap exists so a runaway
# ``snippet_lines`` value can't blow up the response budget.
MAX_SNIPPET_LINES = 100

# Number of context lines added on either side of the chunk's
# declared range so a method's signature and trailing brace are
# visible alongside the body.
_SNIPPET_CONTEXT_LINES = 2

# A light prior that says methods and routes are more interesting
# starting points than raw chunks or generic classes. Tuned by feel
# on the momskitchen fixture — will be revisited after the Phase 5
# external-validation dogfood.
_KIND_WEIGHT: dict[NodeKind, float] = {
    NodeKind.METHOD: 1.20,
    NodeKind.ROUTE: 1.20,
    NodeKind.EVENT: 1.15,
    NodeKind.LISTENER: 1.15,
    NodeKind.JOB: 1.15,
    NodeKind.POLICY: 1.10,
    NodeKind.MODEL: 1.10,
    NodeKind.CONTROLLER: 1.05,
    NodeKind.FORM_REQUEST: 1.05,
    NodeKind.CLASS: 0.95,
    NodeKind.CHUNK: 0.90,
}


class SemanticSearchInput(ToolInput):
    """Free-text semantic search inputs."""

    query: str = Field(
        min_length=1,
        description="Natural-language question to retrieve chunks for.",
    )
    top_k: int = Field(
        default=DEFAULT_TOP_K,
        ge=1,
        le=200,
        description="Candidate window size fetched from the vector store.",
    )
    final_k: int = Field(
        default=DEFAULT_FINAL_K,
        ge=1,
        le=50,
        description="Number of rows returned after graph-aware re-ranking.",
    )
    snippet_lines: int = Field(
        default=DEFAULT_SNIPPET_LINES,
        ge=0,
        le=MAX_SNIPPET_LINES,
        description=(
            "Lines of source code included in each hit's ``snippet`` "
            "field. ``0`` disables snippets entirely (useful when the "
            "agent only wants metadata for a fast triage pass). "
            "Files are read once per call and cached, so the marginal "
            "cost is one ``open()`` per unique file."
        ),
    )
    min_vector_score: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description=(
            "Drop hits whose raw cosine ``vector_score`` is below this "
            "threshold (audit P0-11). Default ``0.4`` filters obvious "
            "gibberish ('aslkdjflaskdjf') while keeping marginal-but-"
            "real matches in. Pair with ``confidence`` on the response "
            "to decide whether a result is worth acting on. When every "
            "candidate falls below the threshold, the tool returns "
            '``error_code="low_relevance"`` instead of padding the '
            "response with weak hits."
        ),
    )


class SemanticHit(ToolOutput):
    """One re-ranked, structurally-annotated search result."""

    node_id: str
    node_kind: str
    node_name: str
    score: float = Field(
        description=(
            "Final re-ranked score = ``vector_score * node_kind_weight``. "
            "Kind weights bias results toward methods/routes (1.20), then "
            "events/listeners/jobs (1.15), then policies/models (1.10), "
            "then controllers/form_requests (1.05); raw chunks and "
            "generic classes are damped (0.95 / 0.90). Use ``score`` for "
            "agent-facing ranking; use ``vector_score`` when comparing "
            "the underlying retrieval quality."
        ),
    )
    vector_score: float = Field(
        description=(
            "Raw cosine similarity from the vector store (0-1). "
            "Higher = closer to the query embedding. Compare this — not "
            "``score`` — when reasoning about whether the embedding "
            "model actually found something semantically relevant."
        ),
    )
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    chunk_id: str | None = None
    container_class: str | None = Field(
        default=None,
        description="FQN of the class a method belongs to, if applicable.",
    )
    related_routes: list[str] = Field(
        default_factory=list,
        description="URIs of routes that target this node, if any.",
    )
    snippet: str | None = Field(
        default=None,
        description=(
            "Source-code preview centred on the chunk's declared "
            "range. Empty string when the file can be opened but the "
            "range is empty; ``None`` when snippets were disabled "
            "(``snippet_lines=0``) or the file couldn't be read."
        ),
    )


class SemanticSearchOutput(ToolOutput):
    """Container for a semantic search response."""

    query: str | None = None
    total_candidates: int = 0
    returned: int = 0
    hits: list[SemanticHit] = Field(default_factory=list)
    confidence: str | None = Field(
        default=None,
        description=(
            "Qualitative confidence in the top hit's relevance, derived "
            "from its raw ``vector_score``: ``high`` (≥ 0.65), ``medium`` "
            "(≥ 0.55), ``low`` (≥ ``min_vector_score`` but below 0.55). "
            "``None`` when no hits were returned (either zero candidates "
            "from the vector store or all candidates filtered by "
            "``min_vector_score``). Audit P0-11."
        ),
    )
    filtered_by_threshold: int = Field(
        default=0,
        description=(
            "Count of candidates dropped because their vector_score was "
            "below ``min_vector_score``. Lets an agent decide whether "
            "to retry with a lower threshold rather than concluding "
            "the corpus has no relevant material."
        ),
    )
    error: str | None = None
    error_code: str | None = None
    truncated: bool = False
    truncated_lists: list[str] = Field(default_factory=list)

    _trimmable_lists: ClassVar[tuple[str, ...]] = ("hits",)


class SemanticSearchTool:
    """Embed → vector search → graph expand → re-rank."""

    name: ClassVar[str] = "semantic_search"
    description: ClassVar[str] = (
        "Free-text semantic search across the indexed chunks with "
        "graph-aware re-ranking. Embeds the query, looks up the nearest "
        "``top_k`` chunks in LanceDB, de-duplicates by graph node, "
        "annotates each with structural context (containing class, "
        "related routes) plus a ``snippet_lines``-line source-code "
        "preview so an agent can verify relevance without re-reading "
        "the file. Returns the top ``final_k`` rows ranked by "
        "``vector_score * node_kind_weight``."
    )
    input_model: ClassVar[type[ToolInput]] = SemanticSearchInput
    output_model: ClassVar[type[ToolOutput]] = SemanticSearchOutput
    # The dominant cost is the embedder call (Ollama / Voyage / OpenAI),
    # not the LanceDB lookup. 2 s leaves headroom for an HTTP-backed
    # embedder on a slow link without flagging every call as
    # over-budget. Local fastembed runs comfortably in under 200 ms.
    latency_budget_ms: ClassVar[int] = 2000

    def execute(
        self,
        payload: SemanticSearchInput,
        ctx: QueryContext,
    ) -> SemanticSearchOutput:
        """Run the full retrieve-expand-rerank pipeline."""
        if ctx.embedder is None:
            return SemanticSearchOutput(
                query=payload.query,
                error="semantic_search requires an embedder on the query context.",
                error_code="no_embedder",
            )
        if ctx.vector_dimensions is None:
            return SemanticSearchOutput(
                query=payload.query,
                error=(
                    "semantic_search needs ``vector_dimensions`` set on the "
                    "query context so it can open the vector store."
                ),
                error_code="no_vector_dimensions",
            )

        query_vector = ctx.embedder.embed([payload.query])[0]
        store = ctx.storage.vectors(dimensions=ctx.vector_dimensions)
        raw_hits = store.search(query_vector, top_k=payload.top_k)
        if not raw_hits:
            return SemanticSearchOutput(
                query=payload.query,
                total_candidates=0,
                returned=0,
            )

        graph = ctx.storage.graph().load()
        aggregated = _aggregate_by_node(raw_hits)
        rows = _build_rows(graph, aggregated)
        # Sort descending by score, breaking ties by node_id ascending.
        # Without the secondary key, two hits at identical vector_scores
        # (common for boilerplate DTOs) would return in
        # implementation-dependent order — flagged by audit P2-21.
        rows.sort(key=lambda r: (-r.score, r.node_id))

        # Audit P0-11: filter low-quality matches before slicing to
        # final_k. Threshold is on the RAW ``vector_score`` (not the
        # kind-weighted ``score``) so the cutoff is calibrated against
        # the embedder's similarity output regardless of kind weights.
        filtered_by_threshold = sum(1 for r in rows if r.vector_score < payload.min_vector_score)
        rows = [r for r in rows if r.vector_score >= payload.min_vector_score]

        if not rows:
            return SemanticSearchOutput(
                query=payload.query,
                total_candidates=len(raw_hits),
                returned=0,
                filtered_by_threshold=filtered_by_threshold,
                error=(
                    f"No hit crossed the relevance threshold "
                    f"``min_vector_score={payload.min_vector_score}``. "
                    f"{len(raw_hits)} candidate(s) fetched; all filtered. "
                    f"Try a more specific query, or lower the threshold "
                    f"(e.g. ``min_vector_score=0.3``) to inspect weak matches."
                ),
                error_code="low_relevance",
            )

        returned = rows[: payload.final_k]
        if payload.snippet_lines > 0:
            returned = _attach_snippets(returned, payload.snippet_lines)

        return SemanticSearchOutput(
            query=payload.query,
            total_candidates=len(raw_hits),
            returned=len(returned),
            hits=returned,
            confidence=_confidence_for(returned[0].vector_score),
            filtered_by_threshold=filtered_by_threshold,
        )


# Audit P0-11: confidence thresholds tuned from synthesq-relay data.
# Real queries clustered 0.60-0.68; gibberish queries ~0.57; genuine
# misses fell below ~0.5. The boundaries are deliberately conservative
# — closer to "is this worth showing the agent at all" than "is this
# the right answer".
_CONFIDENCE_HIGH_THRESHOLD = 0.65
_CONFIDENCE_MEDIUM_THRESHOLD = 0.55


def _confidence_for(top_vector_score: float) -> str:
    """Map a top hit's vector_score to a qualitative confidence label."""
    if top_vector_score >= _CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    if top_vector_score >= _CONFIDENCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Aggregation + re-rank helpers
# ---------------------------------------------------------------------------


class _Aggregate:
    __slots__ = ("best_score", "chunk_id", "hit_payload", "node_id")

    def __init__(
        self,
        *,
        node_id: str,
        best_score: float,
        chunk_id: str,
        hit_payload: dict[str, object],
    ) -> None:
        self.node_id = node_id
        self.best_score = best_score
        self.chunk_id = chunk_id
        self.hit_payload = hit_payload


def _aggregate_by_node(hits: list[VectorSearchHit]) -> list[_Aggregate]:
    """Collapse chunks that share a node id, keeping the best score."""
    by_node: dict[str, _Aggregate] = {}
    for hit in hits:
        node_id = str(hit.payload.get("node_id", "") or "")
        if not node_id:
            # Chunks without a node binding still count — key on chunk id.
            node_id = f"chunk:{hit.id}"
        current = by_node.get(node_id)
        if current is None or hit.score > current.best_score:
            by_node[node_id] = _Aggregate(
                node_id=node_id,
                best_score=hit.score,
                chunk_id=hit.id,
                hit_payload=dict(hit.payload),
            )
    return list(by_node.values())


def _build_rows(graph: Graph, aggregates: list[_Aggregate]) -> list[SemanticHit]:
    """Resolve each aggregate to a graph node and compute annotations."""
    rows: list[SemanticHit] = []
    for agg in aggregates:
        node = graph.node_by_id(agg.node_id)
        if node is None:
            # Dangling reference — keep the row so the chunk isn't lost
            # but mark its kind as ``chunk`` and weight accordingly.
            rows.append(
                SemanticHit(
                    node_id=agg.node_id,
                    node_kind=NodeKind.CHUNK.value,
                    node_name=str(agg.hit_payload.get("symbol") or agg.chunk_id),
                    score=agg.best_score * _KIND_WEIGHT[NodeKind.CHUNK],
                    vector_score=agg.best_score,
                    file=_payload_str(agg.hit_payload, "file_path"),
                    start_line=_payload_int(agg.hit_payload, "start_line"),
                    end_line=_payload_int(agg.hit_payload, "end_line"),
                    chunk_id=agg.chunk_id,
                ),
            )
            continue

        weight = _KIND_WEIGHT.get(node.kind, 1.0)
        rows.append(
            SemanticHit(
                node_id=node.id,
                node_kind=node.kind.value,
                node_name=node.name,
                score=agg.best_score * weight,
                vector_score=agg.best_score,
                file=str_attr(node.attributes, "file")
                or _payload_str(agg.hit_payload, "file_path"),
                start_line=_payload_int(agg.hit_payload, "start_line"),
                end_line=_payload_int(agg.hit_payload, "end_line"),
                chunk_id=agg.chunk_id,
                container_class=_container_class(graph, node),
                related_routes=_related_routes(graph, node),
            ),
        )
    return rows


def _container_class(graph: Graph, node: Node) -> str | None:
    """For method nodes, return the owning class FQN."""
    if node.kind != NodeKind.METHOD:
        return None
    class_fqn = str_attr(node.attributes, "class_fqn")
    if class_fqn is not None:
        return class_fqn
    # Fall back to the PART_OF edge target.
    for edge in outgoing(graph, node.id, EdgeKind.PART_OF):
        target = graph.node_by_id(edge.target)
        if target is not None:
            return str_attr(target.attributes, "fqn") or target.name
    return None


def _related_routes(graph: Graph, node: Node) -> list[str]:
    """Collect URIs of routes pointing at this node (direct or via method)."""
    # Only method nodes are targets of ROUTES_TO in v1.
    if node.kind != NodeKind.METHOD:
        return []
    uris: list[str] = []
    for edge in incoming(graph, node.id, EdgeKind.ROUTES_TO):
        route = graph.node_by_id(edge.source)
        if route is None:
            continue
        uri = str_attr(route.attributes, "uri") or route.name
        methods = str_list_attr(route.attributes, "methods")
        prefix = ",".join(methods) if methods else "?"
        uris.append(f"{prefix} {uri}")
    uris.sort()
    return uris


def _payload_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _payload_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _attach_snippets(
    rows: list[SemanticHit],
    snippet_lines: int,
) -> list[SemanticHit]:
    """Return a new list of rows with ``snippet`` populated.

    Files are read once per call and cached so a result with multiple
    hits in the same file pays one I/O per file. Failures (missing
    file, decode error, hit without a file or line range) yield a
    ``None`` snippet — the caller distinguishes "snippet unavailable"
    from "snippet empty" via the field type, not by reading docs.
    """
    cache: dict[Path, list[str] | None] = {}
    annotated: list[SemanticHit] = []
    for row in rows:
        snippet = _load_snippet(row, cache, snippet_lines)
        annotated.append(row.model_copy(update={"snippet": snippet}))
    return annotated


def _load_snippet(
    row: SemanticHit,
    cache: dict[Path, list[str] | None],
    snippet_lines: int,
) -> str | None:
    """Resolve a single hit's snippet from disk, using the call-scoped cache."""
    if row.file is None or row.start_line is None or row.end_line is None:
        return None

    file_path = Path(row.file)
    if file_path not in cache:
        try:
            cache[file_path] = file_path.read_text(
                encoding="utf-8",
                errors="replace",
            ).splitlines()
        except OSError:
            cache[file_path] = None

    lines = cache[file_path]
    if lines is None:
        return None

    # Convert to 0-indexed slice; widen by the standard context window
    # then clamp to the configured ``snippet_lines`` budget.
    start = max(0, row.start_line - 1 - _SNIPPET_CONTEXT_LINES)
    end = min(len(lines), row.end_line + _SNIPPET_CONTEXT_LINES)
    span = lines[start:end]
    if len(span) > snippet_lines:
        span = span[:snippet_lines]
    return "\n".join(span)
