"""No-op :class:`~nexus.core.protocols.Lsp` implementation.

Used as the fallback when no language server is available on the host
or when the user explicitly opts out via ``--lsp none``. The pipeline
treats LSP enrichment as best-effort: with :class:`NullLsp` the
``enrich_with_lsp`` pass becomes a no-op and CALLS edges are simply
not produced.

The adapter is deliberately stateless - every method either returns an
empty result or returns ``None``. That makes it cheap to construct on
every pipeline run and trivial to reason about in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from nexus.core.lsp import FileLocation


class NullLsp:
    """A no-op LSP that returns no references.

    Conforms structurally to :class:`~nexus.core.protocols.Lsp`. The
    pipeline can substitute this whenever a real language server is
    not configured or not reachable.
    """

    def prepare(self, workspace_root: Path) -> None:
        """No-op. Accepts the workspace path and does nothing with it."""
        _ = workspace_root

    def references(
        self,
        file: Path,
        line: int,
        character: int,
    ) -> list[FileLocation]:
        """Return an empty list of references.

        The return type matches :class:`~nexus.core.protocols.Lsp`
        exactly so the call site cannot tell - at the type level -
        whether it has a real LSP or the null fallback.
        """
        _ = (file, line, character)
        return []

    def close(self) -> None:
        """No-op. There is nothing to release."""
