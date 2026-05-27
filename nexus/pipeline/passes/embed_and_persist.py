"""Final pass: enrich, embed, and persist the chunks + graph.

Takes the graph and chunks the upstream passes produced and writes
them to the project's :class:`~nexus.adapters.storage.ProjectStorage`:

1. Persists the graph atomically to SQLite (one shot - graph writes
   are cheap and bounded by class count, not chunk count).
2. Streams chunks through the embedder in fixed-size batches:

   * enrich the batch's chunks into embedding-input strings
   * look them up in the cache (hits skip the embedder)
   * embed the misses
   * cache the new vectors
   * upsert the batch's vector records into LanceDB
   * emit a progress event for the batch

3. Writes ``meta.json`` with the embedder id and counts.

Batching matters
================

A large project (20k+ chunks) processed as one giant batch holds the
entire enriched-text list + vector list + upcoming LanceDB rows in
memory simultaneously. That costs 5+ GB of RSS on the real CRM
fixture and triggers the OOM killer on a 16 GB machine. Streaming
through fixed-size batches caps memory at O(batch_size) regardless
of project scale.

The pass is skipped quietly if ``ctx.embedder`` is ``None`` - the
pipeline factory may build a pipeline without an embedder when the
caller only wants the graph (tests, dry-run mode).
"""

from __future__ import annotations

import contextlib
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from nexus.adapters.embedders.cache import EmbeddingCache
from nexus.adapters.storage import LanceVectorRecord, ProjectMeta
from nexus.core.chunking import EnrichedTextBuilder
from nexus.core.outcome import Error, Warning
from nexus.pipeline.progress import PassProgress

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from nexus.core.chunking import Chunk
    from nexus.core.graph.graph import Graph
    from nexus.pipeline.context import PipelineContext


def _resolve_git_head(project_path: Path) -> str | None:
    """Return the 40-char git HEAD SHA for the project, or None."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


class EmbedAndPersistPass:
    """Enrich, embed (with cache), and persist everything to storage.

    Processes chunks in fixed-size batches so memory usage is bounded
    by batch size, not project size. On a 20k-chunk enterprise
    project this is the difference between "works" and "OOM-killed
    after 5.7 GB RSS".
    """

    name = "embed_and_persist"

    #: Default number of chunks processed per embed batch. Chosen so a
    #: batch on a modern CPU stays under ~500 MB of RSS while still
    #: being large enough to amortise fastembed's per-call overhead.
    DEFAULT_BATCH_SIZE = 256

    def __init__(
        self,
        *,
        cache: EmbeddingCache | None = None,
        enrichment_builder: EnrichedTextBuilder | None = None,
        batch_size: int | None = None,
    ) -> None:
        """Build the pass.

        Args:
            cache: Embedding cache to use. If ``None``, the pass
                creates a fresh :class:`EmbeddingCache` under a
                temporary directory on the context's storage root -
                useful for tests but wasteful in production, so real
                pipelines should pass an explicit cache.
            enrichment_builder: Override the enrichment builder.
                Defaults to a fresh instance with default knobs.
            batch_size: Chunks processed per batch. Defaults to
                :attr:`DEFAULT_BATCH_SIZE`. Smaller values reduce
                peak memory; larger values amortise embedder startup
                cost.
        """
        self._cache = cache
        self._enrichment = enrichment_builder or EnrichedTextBuilder()
        self._batch_size = batch_size or self.DEFAULT_BATCH_SIZE

    def run(self, ctx: PipelineContext) -> None:
        """Execute the embed + persist step, streaming batches through storage."""
        if ctx.graph is None:
            ctx.add_error(
                Error(
                    code="no_graph",
                    message="EmbedAndPersistPass needs a graph.",
                ),
            )
            return

        if ctx.embedder is None:
            ctx.add_warning(
                Warning(
                    code="no_embedder",
                    message=(
                        "No embedder configured; persisting graph only, "
                        "chunks will not be embedded."
                    ),
                ),
            )
            self._persist_graph_only(ctx)
            return

        cache = self._cache or EmbeddingCache(
            root=ctx.storage.root / "cache" / "embeddings",
        )

        # Persist the graph up front so Phase 4 sees a consistent
        # state even if embedding fails partway through.
        self._persist_graph(ctx, ctx.graph)

        store = ctx.storage.vectors(dimensions=ctx.embedder.dimensions)
        model_id = ctx.embedder.model_id
        total = len(ctx.chunks)

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Embedding {total} chunks in batches of {self._batch_size}",
                total=total,
            ),
        )

        processed = 0
        total_hits = 0
        total_misses = 0

        for batch_index, batch_chunks in enumerate(self._batches(ctx.chunks)):
            enriched = [(chunk, self._enrichment.build(chunk, ctx.graph)) for chunk in batch_chunks]

            hits, misses = cache.get_batch(
                model_id=model_id,
                texts=[text for _, text in enriched],
            )
            pre_cached = len(hits)
            total_hits += pre_cached

            if misses:
                total_misses += len(misses)
                try:
                    new_vectors = ctx.embedder.embed(misses)
                except Exception as e:
                    ctx.add_error(
                        Error(
                            code="embedder_failed",
                            message=(f"Embedding backend raised {type(e).__name__}: {e}"),
                            context={"batch_index": batch_index},
                        ),
                    )
                    return
                new_pairs = dict(zip(misses, new_vectors, strict=True))
                cache.put_batch(model_id=model_id, pairs=new_pairs)
                hits.update(new_pairs)

            records = [
                LanceVectorRecord(
                    id=chunk.id,
                    vector=hits[text],
                    payload={
                        "node_id": chunk.node_id or "",
                        "file_path": str(chunk.file_path),
                        "kind": chunk.kind.value,
                        "symbol": chunk.symbol or "",
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                    },
                )
                for chunk, text in enriched
            ]
            store.upsert(records)

            processed += len(batch_chunks)
            ctx.progress.emit(
                PassProgress(
                    pass_name=self.name,
                    message=(
                        f"Batch {batch_index + 1}: processed {processed} of {total} "
                        f"chunks (cache hits {total_hits}, misses {total_misses})"
                    ),
                    current=processed,
                    total=total,
                    detail={
                        "cache_hits_to_date": total_hits,
                        "cache_misses_to_date": total_misses,
                    },
                ),
            )

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=(
                    f"Persisted {len(ctx.graph.nodes)} nodes, "
                    f"{len(ctx.graph.edges)} edges, {processed} vectors"
                ),
            ),
        )

        self._write_meta(ctx, embedder_id=model_id)

        # Release embedder-owned resources (HTTP pools, loaded model
        # weights) so the pipeline can exit cleanly. Best-effort -
        # an embedder without a close() method is fine.
        close = getattr(ctx.embedder, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()

    def _batches(self, chunks: list[Chunk]) -> Iterator[list[Chunk]]:
        """Yield fixed-size slices of ``chunks``."""
        for start in range(0, len(chunks), self._batch_size):
            yield chunks[start : start + self._batch_size]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _persist_graph_only(self, ctx: PipelineContext) -> None:
        """Persist the graph without embedding chunks.

        Used when no embedder is configured. This is a useful
        fallback for "build me the graph, I'll embed later" flows.
        """
        assert ctx.graph is not None  # enforced by caller
        self._persist_graph(ctx, ctx.graph)
        self._write_meta(ctx, embedder_id=None)

    @staticmethod
    def _persist_graph(ctx: PipelineContext, graph: Graph) -> None:
        graph_store = ctx.storage.graph()
        result = graph_store.persist(graph)
        if not result.ok:
            for err in result.errors:
                ctx.add_error(err)

    @staticmethod
    def _write_meta(ctx: PipelineContext, *, embedder_id: str | None) -> None:
        assert ctx.graph is not None
        laravel_version = ctx.reflection.project.laravel_version if ctx.reflection else None
        meta = ProjectMeta(
            project_slug=ctx.storage.slug,
            project_path=str(ctx.project_path),
            laravel_version=laravel_version,
            last_indexed_commit=_resolve_git_head(ctx.project_path),
            indexed_at=datetime.now(UTC).isoformat(),
            node_count=len(ctx.graph.nodes),
            edge_count=len(ctx.graph.edges),
            embedder_id=embedder_id,
            lsp_server=ctx.lsp_server,
        )
        ctx.storage.write_meta(meta)
