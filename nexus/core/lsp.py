"""Value types shared between the LSP adapter and its consumers.

The :class:`~nexus.core.protocols.Lsp` protocol returns
:class:`FileLocation` instances so callers can stay independent of the
underlying language server's wire format.

Line and character positions are **1-indexed** to match the rest of
Nexus (see :class:`nexus.core.chunking.Chunk.start_line`). LSP servers
report 0-indexed positions per the LSP spec; concrete adapters MUST
convert at the boundary so pure pipeline code uses a single convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileLocation:
    """A range in a source file returned by a language server.

    Attributes:
        file: Absolute path to the source file. Adapters resolve LSP
            ``file://`` URIs to host paths before constructing this.
        start_line: 1-indexed line number of the range start.
        start_character: 1-indexed column number of the range start.
        end_line: 1-indexed line number of the range end (inclusive).
        end_character: 1-indexed column number of the range end.
    """

    file: Path
    start_line: int
    start_character: int
    end_line: int
    end_character: int
