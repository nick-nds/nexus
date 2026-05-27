"""Unit tests for the snippet helper in ``semantic_search``.

Covers the pure I/O slice logic without spinning up an embedder or
LanceDB store. The integration test in
``tests/integration/test_semantic_search.py`` exercises the
end-to-end path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from nexus.core.query.tools.semantic_search import (
    DEFAULT_SNIPPET_LINES,
    MAX_SNIPPET_LINES,
    SemanticHit,
    SemanticSearchInput,
    _attach_snippets,
)

if TYPE_CHECKING:
    from pathlib import Path


def _hit(file: str, start: int, end: int) -> SemanticHit:
    """Build a minimal hit pointing at a file range."""
    return SemanticHit(
        node_id="x",
        node_kind="class",
        node_name="X",
        score=1.0,
        vector_score=0.9,
        file=file,
        start_line=start,
        end_line=end,
    )


# ---------------------------------------------------------------------------
# Input model
# ---------------------------------------------------------------------------


def test_default_snippet_lines_constant_is_a_reasonable_size() -> None:
    """The default fits a typical method signature plus body for an LLM."""
    assert 10 <= DEFAULT_SNIPPET_LINES <= MAX_SNIPPET_LINES


def test_input_accepts_zero_to_disable_snippets() -> None:
    payload = SemanticSearchInput(query="x", snippet_lines=0)
    assert payload.snippet_lines == 0


def test_input_rejects_snippet_lines_above_cap() -> None:
    """A runaway value gets refused at validation time, not silently truncated."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SemanticSearchInput(query="x", snippet_lines=MAX_SNIPPET_LINES + 1)


# ---------------------------------------------------------------------------
# _attach_snippets
# ---------------------------------------------------------------------------


def test_snippet_includes_lines_from_the_declared_range(tmp_path: Path) -> None:
    file = tmp_path / "Foo.php"
    file.write_text(
        "\n".join(
            [
                "<?php",
                "namespace App;",
                "",
                "class Foo",
                "{",
                "    public function bar(): int",
                "    {",
                "        return 42;",
                "    }",
                "}",
            ],
        )
        + "\n",
    )

    hit = _hit(str(file), start=6, end=9)
    [annotated] = _attach_snippets([hit], snippet_lines=30)

    assert annotated.snippet is not None
    assert "public function bar" in annotated.snippet
    assert "return 42" in annotated.snippet


def test_snippet_widens_with_context_lines_around_the_range(tmp_path: Path) -> None:
    """A hit on lines 6-9 sees ~2 lines of context on each side (4-11)."""
    file = tmp_path / "Foo.php"
    text = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    file.write_text(text)

    hit = _hit(str(file), start=6, end=9)
    [annotated] = _attach_snippets([hit], snippet_lines=30)

    assert annotated.snippet is not None
    # _SNIPPET_CONTEXT_LINES is 2, so lines 4..11 inclusive (8 lines)
    assert "line 4" in annotated.snippet
    assert "line 11" in annotated.snippet
    # And not lines outside the window.
    assert "line 3" not in annotated.snippet
    assert "line 12" not in annotated.snippet


def test_snippet_is_capped_at_requested_line_budget(tmp_path: Path) -> None:
    file = tmp_path / "Big.php"
    file.write_text("\n".join(f"line {i}" for i in range(1, 200)) + "\n")

    hit = _hit(str(file), start=10, end=180)  # huge range
    [annotated] = _attach_snippets([hit], snippet_lines=15)

    assert annotated.snippet is not None
    # 15 lines max - the budget wins over the natural range size.
    assert annotated.snippet.count("\n") + 1 == 15


def test_missing_file_yields_none_snippet_without_raising(tmp_path: Path) -> None:
    hit = _hit(str(tmp_path / "does-not-exist.php"), start=1, end=5)

    [annotated] = _attach_snippets([hit], snippet_lines=30)

    assert annotated.snippet is None


def test_hit_without_file_or_range_yields_none_snippet(tmp_path: Path) -> None:
    no_file = SemanticHit(
        node_id="x",
        node_kind="class",
        node_name="X",
        score=1.0,
        vector_score=0.9,
        file=None,
        start_line=1,
        end_line=2,
    )
    no_lines = SemanticHit(
        node_id="x",
        node_kind="class",
        node_name="X",
        score=1.0,
        vector_score=0.9,
        file=str(tmp_path / "x.php"),
        start_line=None,
        end_line=None,
    )

    no_file_out, no_lines_out = _attach_snippets([no_file, no_lines], snippet_lines=30)

    assert no_file_out.snippet is None
    assert no_lines_out.snippet is None


def test_file_read_is_cached_across_hits_in_one_call(tmp_path: Path) -> None:
    """Two hits in the same file → only one disk read."""
    file = tmp_path / "Shared.php"
    file.write_text("\n".join(f"line {i}" for i in range(1, 50)) + "\n")

    reads: list[str] = []
    real_read = type(file).read_text

    def counting_read(self, **kwargs):  # type: ignore[no-untyped-def]
        reads.append(str(self))
        return real_read(self, **kwargs)

    import unittest.mock

    with unittest.mock.patch.object(type(file), "read_text", counting_read):
        hits = [
            _hit(str(file), start=5, end=8),
            _hit(str(file), start=20, end=23),
            _hit(str(file), start=40, end=42),
        ]
        results = _attach_snippets(hits, snippet_lines=30)

    assert len(reads) == 1, f"expected one read for the shared file, got {len(reads)}"
    assert all(r.snippet is not None for r in results)
