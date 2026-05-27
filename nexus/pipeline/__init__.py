"""Indexing pipeline orchestration.

The pipeline is the glue between Phase 2's pure domain code and the
actual work of turning a Laravel project into a queryable index. It
is a thin, explicit sequence of passes:

1. **RunExtractorPass** - invokes ``php artisan nexus:extract`` via the
   subprocess adapter and writes ``reflection.json`` to project storage.
2. **BuildGraphPass** - loads the reflection document and runs the
   :class:`~nexus.core.graph.builder.GraphBuilder`, placing the
   resulting :class:`~nexus.core.graph.Graph` on the context.
3. **ChunkPass** - walks the project's PHP files with tree-sitter and
   produces :class:`~nexus.core.chunking.Chunk` records linked back to
   the graph nodes they came from.
4. **EmbedAndPersistPass** - runs each chunk's enriched text through
   the active embedder (with cache), writes the graph and vectors to
   the project storage, and stamps ``meta.json``.

The orchestrator is intentionally simple: a list of passes, run in
order, sharing a :class:`PipelineContext` by reference. There is no
DAG, no parallelism, no conditional steps. If a pass needs to bail it
records an error on the context and the orchestrator stops there.

Why explicit passes instead of one ``def rebuild()`` function:

* Every pass is individually testable with a stub context.
* The progress reporter (used by the CLI in Phase 5) can subscribe to
  per-pass events without the orchestrator caring about rendering.
* Future passes (LSP enrichment, cost gate, blade chunking) slot in
  without touching the existing ones.
* Change detection for incremental sync can skip or substitute
  specific passes without reinventing the sequence.
"""

from nexus.pipeline.context import PipelineContext
from nexus.pipeline.factory import build_default_pipeline
from nexus.pipeline.orchestrator import Pipeline, PipelineResult
from nexus.pipeline.pass_protocol import Pass
from nexus.pipeline.progress import (
    PassFinished,
    PassProgress,
    PassStarted,
    ProgressEvent,
    ProgressReporter,
)

__all__ = [
    "Pass",
    "PassFinished",
    "PassProgress",
    "PassStarted",
    "Pipeline",
    "PipelineContext",
    "PipelineResult",
    "ProgressEvent",
    "ProgressReporter",
    "build_default_pipeline",
]
