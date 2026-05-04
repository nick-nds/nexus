"""Individual pipeline passes.

Each pass is a small class satisfying the :class:`~nexus.pipeline.pass_protocol.Pass`
protocol. Passes are wired into an ordered list by the pipeline factory
in :mod:`nexus.pipeline.factory` (added later) and run in sequence by
the orchestrator.

Every pass is self-contained: it declares its dependencies via its
constructor, mutates the :class:`~nexus.pipeline.PipelineContext` in
place, and produces progress events via ``ctx.progress``.
"""

from nexus.pipeline.passes.build_graph import BuildGraphPass
from nexus.pipeline.passes.chunk import ChunkPass
from nexus.pipeline.passes.embed_and_persist import EmbedAndPersistPass
from nexus.pipeline.passes.enrich_with_lsp import EnrichWithLspPass
from nexus.pipeline.passes.run_extractor import RunExtractorPass

__all__ = [
    "BuildGraphPass",
    "ChunkPass",
    "EmbedAndPersistPass",
    "EnrichWithLspPass",
    "RunExtractorPass",
]
