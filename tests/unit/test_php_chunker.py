"""Tests for nexus.core.chunking.php_chunker."""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.core.chunking import Chunk, ChunkKind, PhpChunker


@pytest.fixture
def chunker() -> PhpChunker:
    return PhpChunker()


def _source(*lines: str) -> bytes:
    return "\n".join(lines).encode("utf-8")


class TestClassChunking:
    def test_class_with_methods(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "namespace App\\Services;",
            "",
            "final class UserService",
            "{",
            "    public function create(array $data): User",
            "    {",
            "        return new User($data);",
            "    }",
            "",
            "    public function delete(int $id): void",
            "    {",
            "        User::destroy($id);",
            "    }",
            "}",
            "",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/app/Services/UserService.php"),
            source=source,
        )

        # One class header plus two methods.
        kinds = [c.kind for c in chunks]
        assert ChunkKind.CLASS_HEADER in kinds
        assert kinds.count(ChunkKind.METHOD) == 2

    def test_class_header_carries_fqn_node_id(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "namespace App\\Models;",
            "final class User extends Authenticatable",
            "{",
            "    public function posts() {}",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/app/Models/User.php"),
            source=source,
        )

        headers = [c for c in chunks if c.kind == ChunkKind.CLASS_HEADER]
        assert len(headers) == 1
        assert headers[0].node_id == "class:App\\Models\\User"
        assert headers[0].symbol == "User"

    def test_method_chunks_link_to_method_node_id(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "namespace App\\Http;",
            "class FooController {",
            "    public function show(int $id) { return view('foo'); }",
            "    public function store() { return redirect(); }",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/app/Http/FooController.php"),
            source=source,
        )

        methods = {c.symbol: c for c in chunks if c.kind == ChunkKind.METHOD}
        assert "show" in methods
        assert "store" in methods
        assert methods["show"].node_id == "method:App\\Http\\FooController::show"
        assert methods["store"].node_id == "method:App\\Http\\FooController::store"

    def test_class_header_does_not_contain_method_bodies(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "class Big {",
            "    public function a() {",
            "        return 'the-body-text';",
            "    }",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/Big.php"),
            source=source,
        )

        header = next(c for c in chunks if c.kind == ChunkKind.CLASS_HEADER)
        assert "the-body-text" not in header.text


class TestFreeFunctions:
    def test_top_level_function_becomes_function_chunk(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "function greet(string $name): string {",
            "    return 'hi ' . $name;",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/helpers.php"),
            source=source,
        )

        functions = [c for c in chunks if c.kind == ChunkKind.FUNCTION]
        assert len(functions) == 1
        assert functions[0].symbol == "greet"
        # Free functions have no class, so no node id.
        assert functions[0].node_id is None


class TestInterfacesTraitsEnums:
    def test_interface_header_emitted(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "namespace App\\Contracts;",
            "interface Repository",
            "{",
            "    public function find(int $id);",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/Contracts/Repository.php"),
            source=source,
        )

        headers = [c for c in chunks if c.kind == ChunkKind.INTERFACE_HEADER]
        assert len(headers) == 1
        assert headers[0].symbol == "Repository"

    def test_trait_header_emitted(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "trait HasTimestamps {",
            "    public function touch() { }",
            "}",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/HasTimestamps.php"),
            source=source,
        )

        assert any(c.kind == ChunkKind.TRAIT_HEADER for c in chunks)
        # The method inside the trait should also be chunked.
        assert any(c.kind == ChunkKind.METHOD and c.symbol == "touch" for c in chunks)

    def test_enum_header_emitted(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "enum Status: string { case Active = 'active'; case Archived = 'archived'; }",
        )

        chunks = chunker.chunk_source(
            file_path=Path("/Status.php"),
            source=source,
        )

        assert any(c.kind == ChunkKind.ENUM_HEADER for c in chunks)


class TestDeterminism:
    def test_same_source_same_chunks(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "namespace App;",
            "class Foo { public function bar() { return 1; } }",
        )
        path = Path("/Foo.php")

        first = chunker.chunk_source(file_path=path, source=source)
        second = chunker.chunk_source(file_path=path, source=source)

        assert len(first) == len(second)
        for a, b in zip(first, second, strict=True):
            assert a.id == b.id
            assert a.start_byte == b.start_byte
            assert a.end_byte == b.end_byte
            assert a.symbol == b.symbol

    def test_chunk_ids_stable_across_equal_inputs(self) -> None:
        path = Path("/Foo.php")
        first = Chunk.make_id(file_path=path, start_byte=10, end_byte=42)
        second = Chunk.make_id(file_path=path, start_byte=10, end_byte=42)
        assert first == second

    def test_chunk_ids_differ_for_different_ranges(self) -> None:
        path = Path("/Foo.php")
        assert Chunk.make_id(file_path=path, start_byte=10, end_byte=42) != Chunk.make_id(
            file_path=path, start_byte=10, end_byte=43
        )


class TestByteRangesAreCorrect:
    def test_method_text_matches_source_slice(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "class C {",
            "    public function m() {",
            "        return 'x';",
            "    }",
            "}",
        )

        chunks = chunker.chunk_source(file_path=Path("/C.php"), source=source)
        method = next(c for c in chunks if c.kind == ChunkKind.METHOD)

        raw = source[method.start_byte : method.end_byte].decode("utf-8")
        assert raw == method.text
        assert "'x'" in method.text


class TestEdgeCases:
    def test_empty_file(self, chunker: PhpChunker) -> None:
        chunks = chunker.chunk_source(file_path=Path("/empty.php"), source=b"<?php\n")
        assert chunks == []

    def test_only_php_tag(self, chunker: PhpChunker) -> None:
        chunks = chunker.chunk_source(file_path=Path("/short.php"), source=b"<?php")
        assert chunks == []

    def test_top_level_code_without_class(self, chunker: PhpChunker) -> None:
        source = _source(
            "<?php",
            "echo 'hello';",
            "$x = 1;",
        )

        chunks = chunker.chunk_source(file_path=Path("/script.php"), source=source)

        # Top-level statements are not emitted as chunks in Phase 3's
        # initial implementation — nothing to assert beyond "no crash".
        assert isinstance(chunks, list)

    def test_chunk_file_reads_disk(self, chunker: PhpChunker, tmp_path: Path) -> None:
        path = tmp_path / "A.php"
        path.write_text("<?php\nclass A { public function m() {} }\n")

        chunks = chunker.chunk_file(path)

        assert any(c.kind == ChunkKind.CLASS_HEADER for c in chunks)

    def test_chunk_file_missing_returns_empty(self, chunker: PhpChunker, tmp_path: Path) -> None:
        chunks = chunker.chunk_file(tmp_path / "does-not-exist.php")
        assert chunks == []
