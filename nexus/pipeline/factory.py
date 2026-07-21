"""High-level pipeline factory.

:func:`build_default_pipeline` assembles the canonical indexing
pipeline: run the extractor, build the graph, chunk the source,
embed everything, persist. Callers that need a different shape
(dry-run without embedding, reuse an existing reflection) can build
the pass list themselves and pass it to :class:`Pipeline` directly.

:func:`build_post_extraction_pipeline` is the variant used by
:class:`~nexus.pipeline.package_indexer.PackageIndexer` when the
caller has already obtained and normalized a reflection document.
It skips :class:`~nexus.pipeline.passes.RunExtractorPass` and
starts directly from graph building.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.pipeline.orchestrator import Pipeline
from nexus.pipeline.passes import (
    BuildGraphPass,
    ChunkPass,
    EmbedAndPersistPass,
    EnrichWithLspPass,
    RunExtractorPass,
)

if TYPE_CHECKING:
    from nexus.adapters.embedders.cache import EmbeddingCache
    from nexus.adapters.extractor import PhpExtractor
    from nexus.core.graph.builder import GraphBuilder
    from nexus.pipeline.pass_protocol import Pass


def build_default_pipeline(
    *,
    extractor: PhpExtractor | None = None,
    builder: GraphBuilder | None = None,
    cache: EmbeddingCache | None = None,
    batch_size: int | None = None,
) -> Pipeline:
    """Build a :class:`Pipeline` with the four standard passes wired in.

    Args:
        extractor: Optional :class:`PhpExtractor` override. Defaults
            to a stock instance.
        builder: Optional :class:`GraphBuilder` override.
        cache: Optional :class:`EmbeddingCache` override.
        batch_size: Chunks embedded per request. ``None`` uses the
            pass default (256). Lower it for CPU-only embedding so each
            request stays under the embedder timeout.

    Returns:
        A ready-to-run :class:`Pipeline`.
    """
    return Pipeline(
        [
            RunExtractorPass(extractor=extractor),
            BuildGraphPass(builder=builder),
            EnrichWithLspPass(),
            ChunkPass(),
            EmbedAndPersistPass(cache=cache, batch_size=batch_size),
        ],
    )


def build_post_extraction_pipeline(
    *,
    builder: GraphBuilder | None = None,
    cache: EmbeddingCache | None = None,
    include_lsp: bool = False,
) -> Pipeline:
    """Build a :class:`Pipeline` that starts from an already-loaded reflection.

    Used by :class:`~nexus.pipeline.package_indexer.PackageIndexer` when
    the caller has already run extraction and normalized the resulting
    :class:`~nexus.core.reflection.document.ReflectionDocument`. The
    ``RunExtractorPass`` is intentionally omitted - the context must have
    ``ctx.reflection`` populated before :meth:`Pipeline.run` is called.

    LSP enrichment is opt-in via ``include_lsp``. It is off by default
    because a Nexus-driven build extracts from a transient scratch
    Testbench tree, where CALLS enrichment would be meaningless. In-repo
    mode extracts a real checkout on disk, so a language server can
    resolve references there and populate ``CALLS`` edges.

    Args:
        builder: Optional :class:`GraphBuilder` override (tests).
        cache: Optional :class:`EmbeddingCache` override (tests).
        include_lsp: Insert :class:`EnrichWithLspPass` after graph
            construction. The pass reads ``ctx.lsp``; when that is
            ``None`` it degrades to a no-op.

    Returns:
        A ready-to-run :class:`Pipeline` starting from ``BuildGraphPass``.
    """
    passes: list[Pass] = [BuildGraphPass(builder=builder)]
    if include_lsp:
        passes.append(EnrichWithLspPass())
    passes.extend([ChunkPass(), EmbedAndPersistPass(cache=cache)])

    return Pipeline(passes)
