"""Tree-sitter-based PHP source chunker.

Walks a parsed PHP file and emits one :class:`Chunk` per semantic
unit. The walking strategy is deliberately simple: class / interface
/ trait / enum declarations become "header" chunks (containing the
declaration line plus any use-statements but not the method bodies);
each method declaration becomes its own chunk. Free functions become
function chunks. Anything that doesn't fit one of those kinds is
ignored for now - Phase 3's chunker targets the bodies the query
engine most cares about.

Why tree-sitter and not nikic/php-parser:

* Tree-sitter is incremental, which matters for the sync path
  (Phase 3 incremental indexing).
* It's language-agnostic - we get Blade chunking for free via the
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

from typing import TYPE_CHECKING, ClassVar

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
        empty chunk lists - the caller treats absence as "nothing to
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

        Namespace advances forward within a sibling list - a flat
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

    def _walk(  # noqa: PLR0911 - explicit per-type dispatch is the clearest shape
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

        Audit P0-10: the header chunk's ``text`` is a synthesized
        SUMMARY of the class - its docblock + declaration + property
        names + method signatures + enum cases - not just the
        declaration line. Before this change, the chunk was 1-2 lines
        of ``<?php`` + ``final class Foo`` so embedding had nothing to
        match against; multiple DTOs shared identical vector_scores
        because they all looked like boilerplate. The synthesized
        summary gives the retrieval embedder a representative
        signature of what the class IS.

        ``start_byte`` / ``end_byte`` still cover the declaration
        range (not the whole class body), so body-retrieval tools
        like ``get_full_block`` keep showing the right source on
        click-through. Method chunks are still emitted separately
        when the walker descends into ``declaration_list``.
        """
        name = self._extract_name(node, source)
        body = node.child_by_field_name("body")
        header_end_byte = body.start_byte if body else node.end_byte

        # Audit P0-10: synthesize a rich summary text from the class
        # body so the embedder has property names, method signatures,
        # and enum cases to match against.
        header_text = self._build_class_summary(
            node=node,
            source=source,
            body=body,
            declaration_end_byte=header_end_byte,
        )

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
    # Class summary (audit P0-10)
    # ------------------------------------------------------------------

    # Hard cap on body-derived summary lines to keep the embedding input
    # bounded. A class with 200 methods still produces a useful summary
    # but doesn't blow the chunk size out. 80 covers every real-world
    # Laravel class shape we've seen.
    _MAX_BODY_SUMMARY_LINES: ClassVar[int] = 80

    def _build_class_summary(
        self,
        *,
        node: Node,
        source: bytes,
        body: Node | None,
        declaration_end_byte: int,
    ) -> str:
        """Build a retrieval-friendly summary string for a class.

        Layers:
        1. Preceding ``/** ... */`` docblock if present (caller-facing
           prose that explains intent).
        2. The class declaration itself (``final readonly class Foo
           extends Bar implements …``).
        3. A digest of the body - property declarations, method
           signatures, enum cases - one per line, capped at
           ``_MAX_BODY_SUMMARY_LINES``.

        The byte offsets on the resulting chunk still point at the
        declaration only (not the synthesised text), so body
        retrieval continues to land on the source.
        """
        parts: list[str] = []

        docblock = self._preceding_docblock_text(node, source)
        if docblock:
            parts.append(docblock)

        declaration = (
            source[node.start_byte : declaration_end_byte]
            .decode("utf-8", errors="replace")
            .rstrip()
        )
        parts.append(declaration)

        if body is not None:
            body_lines: list[str] = []
            for child in body.children:
                line = self._body_member_summary(child, source)
                if line:
                    body_lines.append(line)
                    if len(body_lines) >= self._MAX_BODY_SUMMARY_LINES:
                        body_lines.append(
                            f"    // … {self._MAX_BODY_SUMMARY_LINES}+ members, truncated",
                        )
                        break
            parts.extend(body_lines)

        return "\n".join(parts)

    @staticmethod
    def _preceding_docblock_text(node: Node, source: bytes) -> str | None:
        """Return the ``/** ... */`` block immediately preceding ``node``, if any.

        Tree-sitter exposes comments as siblings; we walk backwards
        from ``node`` looking at its previous sibling. A leading
        line-comment (``// …``) is ignored - only structured
        docblocks are surfaced into the summary, since those are
        what authors use to explain intent.
        """
        prev = node.prev_named_sibling
        if prev is None or prev.type != "comment":
            return None
        text = source[prev.start_byte : prev.end_byte].decode("utf-8", errors="replace")
        if not text.startswith("/**"):
            return None
        return text

    @staticmethod
    def _body_member_summary(child: Node, source: bytes) -> str | None:
        """Render one body-level member as a single line for the summary.

        Returns ``None`` for child nodes that aren't worth summarising
        (whitespace, closing braces, comments embedded in the body).
        """
        node_type = child.type

        if node_type == "property_declaration":
            # ``private readonly string $name;`` - keep the line as
            # written, stripping any trailing comment/newline noise.
            text = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return "    " + text.strip().splitlines()[0]

        if node_type == "method_declaration":
            # Render the SIGNATURE only - up to the opening brace -
            # not the body, because method bodies get their own
            # dedicated chunks.
            body = child.child_by_field_name("body")
            sig_end = body.start_byte if body is not None else child.end_byte
            text = source[child.start_byte : sig_end].decode("utf-8", errors="replace")
            return "    " + text.strip().splitlines()[0]

        if node_type == "enum_case":
            text = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return "    " + text.strip().splitlines()[0]

        if node_type == "const_declaration":
            text = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return "    " + text.strip().splitlines()[0]

        if node_type == "use_declaration":
            # Trait usage inside a class body - important enough to
            # surface for retrieval ("which classes use HasTimestamps?").
            text = source[child.start_byte : child.end_byte].decode("utf-8", errors="replace")
            return "    " + text.strip().splitlines()[0]

        return None

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
