"""Tests for the class-summary text on class-like chunks (audit P0-10).

Before this change, class header chunks held only the declaration
line - so embedding had almost nothing to match against and adversarial
audits showed real classes losing semantic-search rank to method
bodies. The synthesized summary now packs property names, method
signatures, enum cases, trait usage, constants, and the leading
docblock into the chunk text without inflating the byte range (so
body-retrieval tools still land on the declaration when clicked).
"""

from __future__ import annotations

from pathlib import Path

from nexus.core.chunking.chunk import ChunkKind
from nexus.core.chunking.php_chunker import PhpChunker


def _source(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode("utf-8")


def _class_header(chunks):
    return next(c for c in chunks if c.kind == ChunkKind.CLASS_HEADER)


def test_class_summary_includes_property_declarations() -> None:
    source = _source(
        "<?php",
        "final readonly class Customer {",
        "    public function __construct(",
        "        public string $name,",
        "        public string $email,",
        "    ) {}",
        "    public string $phone;",
        "    private int $age;",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Customer.php"), source=source)
    header = _class_header(chunks)

    # Property declarations appear in the summary text.
    assert "$phone" in header.text
    assert "$age" in header.text


def test_class_summary_includes_method_signatures_but_not_bodies() -> None:
    source = _source(
        "<?php",
        "class SettingsService {",
        "    public function sync(string $key, mixed $value): void",
        "    {",
        "        $secret_implementation_detail = 'this should not embed';",
        "    }",
        "    public function reload(): bool",
        "    {",
        "        return true;",
        "    }",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/SettingsService.php"), source=source)
    header = _class_header(chunks)

    # Signatures present.
    assert "sync(string $key, mixed $value)" in header.text
    assert "reload(): bool" in header.text
    # Method bodies absent - they get their own dedicated chunks.
    assert "secret_implementation_detail" not in header.text


def test_class_summary_includes_enum_cases() -> None:
    source = _source(
        "<?php",
        "enum CustomerStatus: string {",
        "    case Active = 'active';",
        "    case Inactive = 'inactive';",
        "    case Churned = 'churned';",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/CustomerStatus.php"), source=source)
    header = next(c for c in chunks if c.kind == ChunkKind.ENUM_HEADER)

    assert "Active" in header.text
    assert "Inactive" in header.text
    assert "Churned" in header.text
    # Backing values too.
    assert "'active'" in header.text


def test_class_summary_includes_preceding_docblock() -> None:
    source = _source(
        "<?php",
        "/**",
        " * Customer aggregate root - owns identity, contact, and lifecycle state.",
        " */",
        "final class Customer {",
        "    public string $name;",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Customer.php"), source=source)
    header = _class_header(chunks)

    assert "Customer aggregate root" in header.text
    assert "identity, contact, and lifecycle state" in header.text


def test_class_summary_ignores_line_comments() -> None:
    """Only ``/** */`` docblocks are surfaced - ``//`` comments are
    presumed to be local annotations, not class-level prose."""
    source = _source(
        "<?php",
        "// internal helper used during the great migration",
        "final class Customer {",
        "    public string $name;",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Customer.php"), source=source)
    header = _class_header(chunks)

    assert "great migration" not in header.text


def test_class_summary_includes_trait_use() -> None:
    """Surfacing ``use HasTimestamps;`` lets agents ask 'who uses
    HasTimestamps?' via semantic_search."""
    source = _source(
        "<?php",
        "final class Customer {",
        "    use HasTimestamps;",
        "    use HasUuid;",
        "    public string $name;",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Customer.php"), source=source)
    header = _class_header(chunks)

    assert "HasTimestamps" in header.text
    assert "HasUuid" in header.text


def test_class_summary_caps_very_large_classes() -> None:
    """A class with hundreds of methods produces a useful but bounded
    summary - the cap guards against runaway embedding cost."""
    method_lines = []
    for i in range(150):
        method_lines.append(f"    public function method{i:03d}(): void {{}}")
    source = _source("<?php", "class Huge {", *method_lines, "}")
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Huge.php"), source=source)
    header = _class_header(chunks)

    # Cap kicks in: not every method appears.
    assert "method000(): void" in header.text  # early methods present
    assert "method149(): void" not in header.text  # late methods truncated
    # And the truncation marker is visible.
    assert "truncated" in header.text


def test_class_byte_range_still_points_at_declaration_only() -> None:
    """The header chunk's byte range covers only the declaration -
    so body-retrieval tools (get_full_block) display the right
    source even though the embedded text is synthesised."""
    source = _source(
        "<?php",
        "final class Customer {",
        "    public string $name;",
        "    public function full(): string { return $this->name; }",
        "}",
    )
    chunker = PhpChunker()

    chunks = chunker.chunk_source(file_path=Path("/Customer.php"), source=source)
    header = _class_header(chunks)

    # start_line is the class declaration (line 2 - the <?php is line 1).
    assert header.start_line == 2
    # end_line is the opening-brace line, not the closing brace at line 5.
    assert header.end_line == 2
