"""Tree-sitter-based PHP source chunker.

Walks a parsed PHP file and emits one :class:`Chunk` per semantic
unit. The walking strategy is deliberately simple: class / interface
/ trait / enum declarations become "header" chunks (containing the
declaration line plus any use-statements but not the method bodies);
each method declaration becomes its own chunk. Free functions become
function chunks. Anything that doesn't fit one of those kinds is
ignored for now — Phase 3's chunker targets the bodies the query
engine most cares about.

Why tree-sitter and not nikic/php-parser:

* Tree-sitter is incremental, which matters for the sync path
  (Phase 3 incremental indexing).
* It's language-agnostic — we get Blade chunking for free via the
  same engine.
* It's fast and has a stable Python binding.

The chunker is stateless; one instance can process any number of
files. The parser and language objects are built once in
``__init__`` and reused.

Determinism
===========

The walk is depth-first in the order tree-sitter presents children,
which is the source order. Chunk ids (from :meth:`Chunk.make_id`)
are deterministic for a given ``(path, byte_range)``. Running the
chunker twice on the same file produces equal chunk lists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import tree_sitter_php as ts_php
from tree_sitter import Language, Parser

from nexus.core.chunking.chunk import Chunk, ChunkKind

if TYPE_CHECKING:
    from pathlib import Path

    from tree_sitter import Node


_PHP_LANGUAGE = Language(ts_php.language_php())


class PhpChunker:
    """Chunks a PHP source file into retrievable semantic units."""

    def __init__(self) -> None:
        """Construct a chunker with its own parser instance.

        The parser is cheap to build but not thread-safe, so the
        pipeline creates one chunker per thread (currently one total
        since Phase 3 is single-threaded).
        """
        self._parser = Parser(_PHP_LANGUAGE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_file(self, file_path: Path) -> list[Chunk]:
        """Read ``file_path`` from disk and return its chunks.

        Convenience wrapper around :meth:`chunk_source` that handles
        the filesystem read. Files that fail to open are returned as
        empty chunk lists — the caller treats absence as "nothing to
        index" rather than as an error.
        """
        try:
            source = file_path.read_bytes()
        except OSError:
            return []
        return self.chunk_source(file_path=file_path, source=source)

    def chunk_source(self, *, file_path: Path, source: bytes) -> list[Chunk]:
        """Parse ``source`` and return its chunks."""
        tree = self._parser.parse(source)
        chunks: list[Chunk] = []

        # Walk the top-level ``program`` children as a sequence so the
        # flat ``namespace App;`` form (which declares every following
        # top-level sibling under App) can thread its namespace through
        # later siblings. The bracketed ``namespace App { ... }`` form
        # puts the classes as children of the namespace node and is
        # handled recursively inside :meth:`_walk_sequence`.
        self._walk_sequence(
            nodes=list(tree.root_node.children),
            source=source,
            file_path=file_path,
            namespace=None,
            class_stack=[],
            out=chunks,
        )

        return chunks

    # ------------------------------------------------------------------
    # Walker
    # ------------------------------------------------------------------

    def _walk_sequence(
        self,
        *,
        nodes: list[Node],
        source: bytes,
        file_path: Path,
        namespace: str | None,
        class_stack: list[str],
        out: list[Chunk],
    ) -> None:
        """Walk a sequence of sibling nodes, threading namespace state.

        Namespace advances forward within a sibling list — a flat
        ``namespace App;`` declaration updates ``current_namespace``
        for every later sibling in the same scope.
        """
        current_namespace = namespace
        for child in nodes:
            next_namespace = self._walk(
                node=child,
                source=source,
                file_path=file_path,
                namespace=current_namespace,
                class_stack=class_stack,
                out=out,
            )
            if next_namespace is not None:
                current_namespace = next_namespace

    def _walk(  # noqa: PLR0911 — explicit per-type dispatch is the clearest shape
        self,
        *,
        node: Node,
        source: bytes,
        file_path: Path,
        namespace: str | None,
        class_stack: list[str],
        out: list[Chunk],
    ) -> str | None:
        """Process a single node.

        Returns a new namespace string when this node is a flat
        ``namespace Foo;`` declaration (which affects subsequent
        siblings), otherwise ``None``. Bracketed namespaces handle
        their own scope via recursive descent and return ``None``.
        """
        node_type = node.type

        if node_type == "namespace_definition":
            new_namespace = self._extract_namespace(node, source)
            body = node.child_by_field_name("body")
            if body is not None:
                # Bracketed form: walk the body's children with the
                # namespace scoped to that subtree only.
                self._walk_sequence(
                    nodes=list(body.children),
                    source=source,
                    file_path=file_path,
                    namespace=new_namespace or namespace,
                    class_stack=class_stack,
                    out=out,
                )
                return None
            # Flat form: return the namespace so the caller applies
            # it to subsequent siblings.
            return new_namespace or namespace

        if node_type == "class_declaration":
            self._emit_and_descend_into_declaration(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_stack=class_stack,
                header_kind=ChunkKind.CLASS_HEADER,
                out=out,
            )
            return None

        if node_type == "interface_declaration":
            self._emit_and_descend_into_declaration(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_stack=class_stack,
                header_kind=ChunkKind.INTERFACE_HEADER,
                out=out,
            )
            return None

        if node_type == "trait_declaration":
            self._emit_and_descend_into_declaration(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_stack=class_stack,
                header_kind=ChunkKind.TRAIT_HEADER,
                out=out,
            )
            return None

        if node_type == "enum_declaration":
            self._emit_and_descend_into_declaration(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_stack=class_stack,
                header_kind=ChunkKind.ENUM_HEADER,
                out=out,
            )
            return None

        if node_type == "method_declaration" and class_stack:
            self._emit_method(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_name=class_stack[-1],
                out=out,
            )
            return None

        if node_type == "function_definition":
            self._emit_function(
                node=node,
                source=source,
                file_path=file_path,
                namespace=namespace,
                out=out,
            )
            return None

        # Default: descend into this node's children as a sequence.
        self._walk_sequence(
            nodes=list(node.children),
            source=source,
            file_path=file_path,
            namespace=namespace,
            class_stack=class_stack,
            out=out,
        )
        return None

    # ------------------------------------------------------------------
    # Emitters
    # ------------------------------------------------------------------

    def _emit_and_descend_into_declaration(
        self,
        *,
        node: Node,
        source: bytes,
        file_path: Path,
        namespace: str | None,
        class_stack: list[str],
        header_kind: ChunkKind,
        out: list[Chunk],
    ) -> None:
        """Emit a header chunk for a class-like declaration and recurse.

        The header chunk covers the declaration line(s) up to (but
        not including) the opening brace's body. Method chunks are
        emitted separately when the walker descends into
        ``declaration_list``. Without this split a single-file class
        of 500 lines would end up as one 500-line chunk, which is
        exactly the wrong granularity for retrieval.
        """
        name = self._extract_name(node, source)
        body = node.child_by_field_name("body")
        header_end_byte = body.start_byte if body else node.end_byte
        header_text = source[node.start_byte : header_end_byte].decode("utf-8", errors="replace")

        fqn = self._fqn(namespace, name) if name else None
        class_node_id = f"class:{fqn}" if fqn else None
        header_id = Chunk.make_id(
            file_path=file_path,
            start_byte=node.start_byte,
            end_byte=header_end_byte,
        )

        header = Chunk(
            id=header_id,
            kind=header_kind,
            file_path=file_path,
            start_byte=node.start_byte,
            end_byte=header_end_byte,
            start_line=node.start_point[0] + 1,
            end_line=((body.start_point[0] + 1) if body else node.end_point[0] + 1),
            text=header_text,
            node_id=class_node_id,
            symbol=name,
            attributes={"namespace": namespace or ""},
        )
        out.append(header)

        # Descend into the class body with the class name pushed.
        new_stack = [*class_stack, fqn] if fqn else class_stack
        if body is not None:
            self._walk_sequence(
                nodes=list(body.children),
                source=source,
                file_path=file_path,
                namespace=namespace,
                class_stack=new_stack,
                out=out,
            )

    def _emit_method(
        self,
        *,
        node: Node,
        source: bytes,
        file_path: Path,
        namespace: str | None,
        class_name: str,
        out: list[Chunk],
    ) -> None:
        method_name = self._extract_name(node, source) or "<anonymous>"
        text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        node_id = f"method:{class_name}::{method_name}"

        out.append(
            Chunk(
                id=Chunk.make_id(
                    file_path=file_path,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                ),
                kind=ChunkKind.METHOD,
                file_path=file_path,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                text=text,
                node_id=node_id,
                symbol=method_name,
                attributes={"class": class_name, "namespace": namespace or ""},
            ),
        )

    def _emit_function(
        self,
        *,
        node: Node,
        source: bytes,
        file_path: Path,
        namespace: str | None,
        out: list[Chunk],
    ) -> None:
        name = self._extract_name(node, source) or "<anonymous>"
        text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

        out.append(
            Chunk(
                id=Chunk.make_id(
                    file_path=file_path,
                    start_byte=node.start_byte,
                    end_byte=node.end_byte,
                ),
                kind=ChunkKind.FUNCTION,
                file_path=file_path,
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                text=text,
                node_id=None,
                symbol=name,
                attributes={"namespace": namespace or ""},
            ),
        )

    # ------------------------------------------------------------------
    # Small helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_name(node: Node, source: bytes) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _extract_namespace(node: Node, source: bytes) -> str | None:
        """Return the namespace name from a ``namespace_definition`` node."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        return source[name_node.start_byte : name_node.end_byte].decode("utf-8", errors="replace")

    @staticmethod
    def _fqn(namespace: str | None, short_name: str) -> str:
        if namespace:
            return f"{namespace}\\{short_name}"
        return short_name
