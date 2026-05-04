"""The :class:`Chunk` value type.

A chunk is one retrievable unit of code — typically a method body or
a small class — tagged with enough metadata that the embedding and
retrieval layers can map it back to a graph node.

Design notes
============

* **Stable id.** The id is deterministic (``sha1`` of file path +
  byte range) so rebuilds produce the same ids and the embedding
  cache stays hot across runs.
* **Byte range, not line range.** Byte offsets survive CRLF /
  trailing-whitespace edits more robustly than line numbers and
  align with tree-sitter's own addressing.
* **Node linkage is optional.** A chunk that the chunker couldn't
  attribute to a graph node (top-level script code, namespaced
  constants) still gets emitted with ``node_id=None``; downstream
  consumers filter or use the file reference instead.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path  # noqa: TC003 — dataclass field runtime type


class ChunkKind(StrEnum):
    """The shape of code a chunk represents.

    Used for filtering and for tuning per-kind priorities in the
    retrieval re-ranker (Phase 4).
    """

    METHOD = "method"
    FUNCTION = "function"
    CLASS_HEADER = "class_header"  # class/interface/trait declaration minus its methods
    INTERFACE_HEADER = "interface_header"
    TRAIT_HEADER = "trait_header"
    ENUM_HEADER = "enum_header"
    FILE = "file"  # fallback when no finer unit fits


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable code unit.

    Attributes:
        id: Deterministic SHA-1 prefix derived from file path plus
            byte range. Used as the vector store key and the
            embedding cache key component.
        kind: One of :class:`ChunkKind`.
        file_path: Absolute path to the source file.
        start_byte: Inclusive byte offset of the chunk's first character.
        end_byte: Exclusive byte offset of the chunk's last character.
        start_line: 1-indexed start line, for human-readable display.
        end_line: 1-indexed end line.
        text: The raw source text of the chunk. Stored verbatim so
            the enrichment step has the full content to work with.
        node_id: Graph node id this chunk belongs to, if any. For a
            method chunk this is typically ``method:<class>::<name>``.
            For a class-header chunk it's ``class:<fqn>``.
        symbol: Short human-readable name (the method name, the class
            short name). Used for progress output and for the
            enrichment template header.
    """

    id: str
    kind: ChunkKind
    file_path: Path
    start_byte: int
    end_byte: int
    start_line: int
    end_line: int
    text: str
    node_id: str | None = None
    symbol: str | None = None
    attributes: dict[str, object] = field(default_factory=dict, hash=False, compare=False)

    @property
    def byte_length(self) -> int:
        """Number of bytes the chunk spans."""
        return self.end_byte - self.start_byte

    @property
    def line_count(self) -> int:
        """Inclusive number of lines the chunk covers."""
        return self.end_line - self.start_line + 1

    @classmethod
    def make_id(cls, *, file_path: Path, start_byte: int, end_byte: int) -> str:
        """Build the deterministic id for a chunk.

        Exposed as a class method so other callers (e.g. the
        incremental change classifier in Phase 3's sync path) can
        compute the same id without instantiating a Chunk.
        """
        payload = f"{file_path}:{start_byte}:{end_byte}".encode()
        return hashlib.sha1(payload, usedforsecurity=False).hexdigest()[:16]
