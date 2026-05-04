"""Mutable context object threaded through every pipeline pass.

A :class:`PipelineContext` carries every piece of state a pass might
need: the project path, the active :class:`ProjectStorage`, the
embedder, the progress reporter, the current in-flight graph/chunks,
and an accumulator for warnings and errors.

The shape is deliberately flat and mutable. Passes set fields as they
produce their outputs (``context.graph``, ``context.chunks``) and the
orchestrator checks ``context.errors`` between passes to decide
whether to continue.

Why not dependency injection per pass?
======================================

Threading the same context through each pass keeps the pass signature
narrow (``run(ctx) -> None``) and lets downstream passes read whatever
upstream passes produced without extra plumbing. A richer alternative
— one input type and one output type per pass, with the orchestrator
explicitly linking them — is noise we don't need for a linear pipeline
with no branching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nexus.pipeline.progress import NullProgressReporter

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.adapters.storage.project_storage import ProjectStorage
    from nexus.core.chunking import Chunk
    from nexus.core.graph.graph import Graph
    from nexus.core.outcome import Error, Warning
    from nexus.core.protocols import Embedder, Lsp, Profile
    from nexus.core.reflection.document import ReflectionDocument
    from nexus.pipeline.progress import ProgressReporter


@dataclass(slots=True)
class PipelineContext:
    """Shared state for one indexing run.

    Required at construction:
        project_path: Absolute path to the Laravel project directory.
        storage: The per-project storage repository this run writes to.
        profile: The active profile (loaded from a built-in or
            ``nexus.yml``).

    Optional at construction:
        embedder: The embedder backend. Required before the
            EmbedAndPersistPass runs but not before earlier passes,
            so the orchestrator can set it lazily.
        lsp: The language-server backend used by EnrichWithLspPass to
            populate CALLS edges. ``None`` (the default) disables the
            pass — the pipeline still runs, but no CALLS edges are
            produced.
        lsp_server: A descriptive label for the LSP backend (binary
            path or name) recorded in ``meta.json`` so ``index status``
            can show what enrichment ran. ``None`` when no LSP was
            used.
        progress: A :class:`ProgressReporter`. Defaults to a
            :class:`NullProgressReporter` so pass code can always call
            ``context.progress.emit(...)`` without a nil check.
        include_tests: Whether to pass ``--include-tests`` to the PHP
            extractor. Defaults to ``False`` matching the Phase 1
            safety default.

    Populated by passes as they run:
        reflection: Set by RunExtractorPass after the extractor has
            written its output and we've parsed it.
        graph: Set by BuildGraphPass. Downstream passes read it.
        chunks: Set by ChunkPass.

    Accumulators:
        warnings: Non-fatal issues surfaced during the run. Collected
            from individual passes and from the graph builder's own
            outcome warnings.
        errors: Fatal issues that should halt the pipeline. The
            orchestrator checks this between passes.
    """

    project_path: Path
    storage: ProjectStorage
    profile: Profile
    embedder: Embedder | None = None
    lsp: Lsp | None = None
    lsp_server: str | None = None
    progress: ProgressReporter = field(default_factory=NullProgressReporter)
    include_tests: bool = False

    reflection: ReflectionDocument | None = None
    graph: Graph | None = None
    chunks: list[Chunk] = field(default_factory=list)

    warnings: list[Warning] = field(default_factory=list)
    errors: list[Error] = field(default_factory=list)

    def ok(self) -> bool:
        """Whether the pipeline has not yet hit any fatal error."""
        return not self.errors

    def add_warning(self, warning: Warning) -> None:
        """Append a warning (does not flip :meth:`ok`)."""
        self.warnings.append(warning)

    def add_error(self, error: Error) -> None:
        """Append an error (flips :meth:`ok` to ``False``)."""
        self.errors.append(error)
