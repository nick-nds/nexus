"""Semantic chunking of project source code.

Phase 3's chunker takes raw PHP source files and splits them at
class/method boundaries via tree-sitter. Each chunk becomes one
row in the vector store, so chunk quality directly drives
retrieval quality.

Two concerns live here:

* :mod:`nexus.core.chunking.chunk` - the :class:`Chunk` dataclass
  carrying the text, byte range, graph-node linkage, and source
  file. Pure data; no behaviour.
* :mod:`nexus.core.chunking.php_chunker` - the tree-sitter driver
  that walks a parsed file and emits one chunk per semantic unit.

The enrichment pass (turning a chunk into the actual embedding
input string) is a separate concern - see
:mod:`nexus.core.chunking.enrichment`.
"""

from nexus.core.chunking.chunk import Chunk, ChunkKind
from nexus.core.chunking.enrichment import EnrichedTextBuilder
from nexus.core.chunking.php_chunker import PhpChunker

__all__ = ["Chunk", "ChunkKind", "EnrichedTextBuilder", "PhpChunker"]
