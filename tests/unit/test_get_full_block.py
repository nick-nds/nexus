"""Unit tests for ``get_full_block``.

Covers the file-read + range-slice + path-containment logic without
needing a graph or vector store. The companion
``test_get_node_body.py`` exercises node-id resolution; the
integration test in ``tests/integration/test_query_tools_body_retrieval.py``
exercises end-to-end through the registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.core.query.budget import ResponseBudget
from nexus.core.query.context import QueryContext
from nexus.core.query.coverage import Coverage
from nexus.core.query.tools.get_full_block import (
    GetFullBlockInput,
    GetFullBlockTool,
)
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


SAMPLE_PHP = """<?php

namespace App\\Demo;

class Foo
{
    public function rules(): array
    {
        return [
            'name' => ['required', 'string', 'max:255'],
            'sku' => ['required', 'string', 'max:100', 'unique:products'],
        ];
    }

    public function messages(): array
    {
        return ['name.required' => 'The name field is required.'];
    }
}
"""


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return root


@pytest.fixture
def sample_file(project_root: Path) -> Path:
    file = project_root / "app" / "Foo.php"
    file.parent.mkdir(parents=True)
    file.write_text(SAMPLE_PHP, encoding="utf-8")
    return file


class _StubStorage:
    """Minimal storage stub. get_full_block does not need graph/vectors."""

    slug = "stub"

    def graph(self) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    def vectors(self, *, dimensions: int) -> object:  # pragma: no cover - unused
        raise NotImplementedError


def _ctx(project_root: Path | None, *, indexed_at: str | None = None) -> QueryContext:
    """Build a QueryContext whose coverage has the given project_path."""
    coverage: Coverage | None = None
    if project_root is not None or indexed_at is not None:
        coverage = Coverage(
            project_path=str(project_root) if project_root is not None else None,
            indexed_at=indexed_at,
        )
    return QueryContext(
        storage=_StubStorage(),  # type: ignore[arg-type]
        budget=ResponseBudget(),
        coverage=coverage,
    )


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


def test_input_rejects_start_line_below_one() -> None:
    with pytest.raises(ValidationError):
        GetFullBlockInput(file_path="/x", start_line=0, end_line=5)


def test_input_rejects_end_line_below_one() -> None:
    with pytest.raises(ValidationError):
        GetFullBlockInput(file_path="/x", start_line=1, end_line=0)


def test_input_rejects_negative_context_lines() -> None:
    with pytest.raises(ValidationError):
        GetFullBlockInput(file_path="/x", start_line=1, end_line=2, context_lines=-1)


def test_input_rejects_context_lines_above_cap() -> None:
    with pytest.raises(ValidationError):
        GetFullBlockInput(file_path="/x", start_line=1, end_line=2, context_lines=100)


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_returns_exact_range_content(sample_file: Path, project_root: Path) -> None:
    tool = GetFullBlockTool()
    # SAMPLE_PHP: line 7 is ``public function rules(): array``, line 13
    # is the method's closing brace.
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=7,
        end_line=13,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error is None
    assert out.content is not None
    lines = out.content.splitlines()
    assert lines[0].strip().startswith("public function rules")
    assert lines[1].strip() == "{"
    assert "return [" in lines[2]
    assert lines[-1].strip() == "}"
    assert out.start_line == 7
    assert out.end_line == 13
    assert out.line_count == 7
    assert out.total_file_lines == len(SAMPLE_PHP.splitlines())


def test_context_lines_widens_window(sample_file: Path, project_root: Path) -> None:
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=7,
        end_line=13,
        context_lines=2,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error is None
    assert out.start_line == 5  # widened upward by 2
    assert out.end_line == 15  # widened downward by 2
    assert out.line_count == 11
    assert out.content is not None
    # The widened span now includes the class declaration above.
    assert "class Foo" in out.content


def test_clamps_end_line_to_eof(sample_file: Path, project_root: Path) -> None:
    tool = GetFullBlockTool()
    total = len(SAMPLE_PHP.splitlines())

    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=total + 50,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error is None
    assert out.end_line == total
    assert out.line_count == total
    assert out.truncated_to_eof is True


def test_clamps_start_line_above_eof_yields_empty(sample_file: Path, project_root: Path) -> None:
    tool = GetFullBlockTool()
    total = len(SAMPLE_PHP.splitlines())

    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=total + 5,
        end_line=total + 10,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "range_out_of_bounds"
    assert out.content is None


# ---------------------------------------------------------------------------
# Range errors
# ---------------------------------------------------------------------------


def test_end_before_start_returns_invalid_range(sample_file: Path, project_root: Path) -> None:
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=10,
        end_line=5,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "invalid_range"
    assert out.content is None


# ---------------------------------------------------------------------------
# File errors
# ---------------------------------------------------------------------------


def test_missing_file_returns_file_not_found(project_root: Path) -> None:
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(project_root / "no" / "such.php"),
        start_line=1,
        end_line=10,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "file_not_found"
    assert out.content is None


def test_directory_path_returns_file_not_found(project_root: Path) -> None:
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(project_root),
        start_line=1,
        end_line=10,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "file_not_found"


# ---------------------------------------------------------------------------
# Path containment (security gate)
# ---------------------------------------------------------------------------


def test_file_outside_project_root_is_rejected(
    sample_file: Path,
    project_root: Path,
    tmp_path: Path,
) -> None:
    """A path resolving outside the indexed project must be refused."""
    outside = tmp_path / "outside.php"
    outside.write_text("<?php\n", encoding="utf-8")
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(outside),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "file_outside_project"
    assert out.content is None


def test_symlink_pointing_outside_project_is_rejected(
    project_root: Path,
    tmp_path: Path,
) -> None:
    """Symlinks that resolve outside the project are caught by Path.resolve()."""
    outside_target = tmp_path / "secret.txt"
    outside_target.write_text("secret\n", encoding="utf-8")
    link = project_root / "link.php"
    link.symlink_to(outside_target)
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(link),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error_code == "file_outside_project"


def test_no_project_root_allows_any_path(sample_file: Path) -> None:
    """When coverage has no project_path, the containment gate is bypassed."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(None))

    assert out.error is None
    assert out.content is not None


# ---------------------------------------------------------------------------
# Relative paths
# ---------------------------------------------------------------------------


def test_relative_path_resolved_against_project_root(
    sample_file: Path,
    project_root: Path,
) -> None:
    """A relative path is joined onto the project root before reading."""
    tool = GetFullBlockTool()
    relative = sample_file.relative_to(project_root)
    payload = GetFullBlockInput(
        file_path=str(relative),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error is None
    assert out.content is not None


def test_relative_path_without_project_root_returns_file_not_found(tmp_path: Path) -> None:
    """No project_path + relative path = ambiguous → file_not_found."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path="app/Foo.php",
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(None))

    assert out.error_code == "file_not_found"


# ---------------------------------------------------------------------------
# file_mtime_utc + chunk_may_be_stale (reporter feedback)
# ---------------------------------------------------------------------------


def test_returns_file_mtime_in_output(sample_file: Path, project_root: Path) -> None:
    """Every successful read carries the file's mtime as ISO-8601 UTC."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root))

    assert out.error is None
    assert out.file_mtime_utc is not None
    # ISO-8601 with a UTC offset
    assert "T" in out.file_mtime_utc
    assert out.file_mtime_utc.endswith("+00:00") or out.file_mtime_utc.endswith("Z")


def test_chunk_may_be_stale_when_file_mtime_after_indexed_at(
    sample_file: Path, project_root: Path
) -> None:
    """Index built in the past, file edited today → stale signal fires."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root, indexed_at="2020-01-01T00:00:00+00:00"))

    assert out.error is None
    assert out.chunk_may_be_stale is True


def test_chunk_not_stale_when_file_mtime_before_indexed_at(
    sample_file: Path, project_root: Path
) -> None:
    """Index built today, file untouched → no staleness signal."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    # Use a far-future indexed_at so any test file mtime is before it
    out = tool.execute(payload, _ctx(project_root, indexed_at="2099-12-31T23:59:59+00:00"))

    assert out.error is None
    assert out.chunk_may_be_stale is False


def test_chunk_may_be_stale_false_when_no_indexed_at(sample_file: Path, project_root: Path) -> None:
    """No indexed_at on coverage → can't prove staleness, default to False."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(project_root))  # no indexed_at

    assert out.error is None
    assert out.file_mtime_utc is not None  # still returned
    assert out.chunk_may_be_stale is False


def test_chunk_may_be_stale_false_when_no_coverage_at_all(sample_file: Path) -> None:
    """No coverage object → graceful False, file_mtime_utc still surfaced."""
    tool = GetFullBlockTool()
    payload = GetFullBlockInput(
        file_path=str(sample_file),
        start_line=1,
        end_line=1,
    )

    out = tool.execute(payload, _ctx(None))

    assert out.error is None
    assert out.file_mtime_utc is not None
    assert out.chunk_may_be_stale is False
