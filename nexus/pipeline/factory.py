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


def build_default_pipeline(
    *,
    extractor: PhpExtractor | None = None,
    builder: GraphBuilder | None = None,
    cache: EmbeddingCache | None = None,
) -> Pipeline:
    """Build a :class:`Pipeline` with the four standard passes wired in.

    Args:
        extractor: Optional :class:`PhpExtractor` override. Defaults
            to a stock instance.
        builder: Optional :class:`GraphBuilder` override.
        cache: Optional :class:`EmbeddingCache` override.

    Returns:
        A ready-to-run :class:`Pipeline`.
    """
    return Pipeline(
        [
            RunExtractorPass(extractor=extractor),
            BuildGraphPass(builder=builder),
            EnrichWithLspPass(),
            ChunkPass(),
            EmbedAndPersistPass(cache=cache),
        ],
    )


def build_post_extraction_pipeline(
    *,
    builder: GraphBuilder | None = None,
    cache: EmbeddingCache | None = None,
) -> Pipeline:
    """Build a :class:`Pipeline` that starts from an already-loaded reflection.

    Used by :class:`~nexus.pipeline.package_indexer.PackageIndexer` when
    the caller has already run extraction and normalized the resulting
    :class:`~nexus.core.reflection.document.ReflectionDocument`. The
    ``RunExtractorPass`` is intentionally omitted — the context must have
    ``ctx.reflection`` populated before :meth:`Pipeline.run` is called.

    LSP enrichment is also omitted: Testbench-booted packages run inside
    a scratch environment where no language server is wired up.

    Args:
        builder: Optional :class:`GraphBuilder` override (tests).
        cache: Optional :class:`EmbeddingCache` override (tests).

    Returns:
        A ready-to-run :class:`Pipeline` starting from ``BuildGraphPass``.
    """
    return Pipeline(
        [
            BuildGraphPass(builder=builder),
            ChunkPass(),
            EmbedAndPersistPass(cache=cache),
        ],
    )
