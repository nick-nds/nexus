"""High-level pipeline factory.

:func:`build_default_pipeline` assembles the canonical indexing
pipeline: run the extractor, build the graph, chunk the source,
embed everything, persist. Callers that need a different shape
(dry-run without embedding, reuse an existing reflection) can build
the pass list themselves and pass it to :class:`Pipeline` directly.
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
