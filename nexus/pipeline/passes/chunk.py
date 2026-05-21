"""Third pass: walk project PHP files and produce chunks.

Uses the :class:`~nexus.core.chunking.PhpChunker` against every
project-scope file recorded in the reflection document's ``classes``
section. We trust the reflection for file discovery because Phase 1's
``ClassMapWalker`` already handled vendor exclusion, the ``tests/``
skip default, and autoload resolution — duplicating that walking
here would risk diverging behaviour.

Files that fail to open or parse are recorded as warnings and the
rest of the set is processed normally. The chunker itself returns an
empty list on read failure, so the pass rarely needs to intervene.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from nexus.core.chunking import Chunk, PhpChunker
from nexus.core.outcome import Warning
from nexus.pipeline.progress import PassProgress

if TYPE_CHECKING:
    from nexus.pipeline.context import PipelineContext


class ChunkPass:
    """Chunk every project PHP file reachable from the reflection document."""

    name = "chunk"

    def __init__(self, chunker: PhpChunker | None = None) -> None:
        """Build the pass with an optional chunker override."""
        self._chunker = chunker or PhpChunker()

    def run(self, ctx: PipelineContext) -> None:
        """Chunk each project file and append :class:`Chunk` instances to ``ctx``."""
        if ctx.reflection is None or ctx.reflection.sections.classes is None:
            ctx.add_warning(
                Warning(
                    code="no_classes_section",
                    message="No classes section in reflection; nothing to chunk.",
                ),
            )
            ctx.chunks = []
            return

        # Build a de-duplicated list of project files. The reflection
        # document may reference the same file from multiple class
        # entries (e.g. a file declaring two interfaces).
        #
        # The PathNormalizer writes relative paths into the reflection
        # (``src/Foo.php``), so we resolve them against
        # ``ctx.project_path`` here rather than trusting the CLI's CWD
        # to match the project root. Package-mode indexing exposed this
        # — the user typically runs ``nexus package index`` from outside
        # the target package, and bare relative paths would silently
        # produce zero chunks.
        project_root = ctx.project_path
        seen: set[Path] = set()
        for entry in ctx.reflection.sections.classes.items:
            if entry.source != "project":
                continue
            if entry.reflection.file is None:
                continue
            file_path = Path(entry.reflection.file)
            if not file_path.is_absolute():
                file_path = project_root / file_path
            seen.add(file_path)

        files: list[Path] = sorted(seen)

        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Chunking {len(files)} PHP files",
                total=len(files),
            ),
        )

        chunks: list[Chunk] = []
        for i, file_path in enumerate(files, start=1):
            file_chunks = self._chunker.chunk_file(file_path)
            chunks.extend(file_chunks)
            if i % 100 == 0 or i == len(files):
                ctx.progress.emit(
                    PassProgress(
                        pass_name=self.name,
                        message=f"Chunked {i} of {len(files)} files",
                        current=i,
                        total=len(files),
                    ),
                )

        ctx.chunks = list(chunks)
        ctx.progress.emit(
            PassProgress(
                pass_name=self.name,
                message=f"Produced {len(chunks)} chunks from {len(files)} files",
                detail={"chunks": len(chunks), "files": len(files)},
            ),
        )
