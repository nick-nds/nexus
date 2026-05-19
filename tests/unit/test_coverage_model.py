"""Unit tests for :class:`nexus.core.query.coverage.Coverage`.

Covers the from-meta translation and the all-defaults fallback.
The integration of coverage into actual tool responses is tested in
``tests/unit/test_query_engine_coverage.py`` against a stub tool.
"""

from __future__ import annotations

from nexus.adapters.storage import ProjectMeta
from nexus.core.query.coverage import Coverage


def test_from_meta_with_lsp_marks_calls_indexed_true() -> None:
    meta = ProjectMeta(
        project_slug="x",
        project_path="/tmp/x",
        lsp_server="/usr/local/bin/intelephense",
        embedder_id="ollama:nomic-embed-text",
        indexed_at="2026-05-03T12:00:00+00:00",
    )

    coverage = Coverage.from_meta(meta)

    assert coverage.calls_indexed is True
    assert coverage.lsp_server == "/usr/local/bin/intelephense"
    assert coverage.embedder_id == "ollama:nomic-embed-text"
    assert coverage.indexed_at == "2026-05-03T12:00:00+00:00"
    assert coverage.project_path == "/tmp/x"


def test_from_meta_without_lsp_marks_calls_indexed_false() -> None:
    """No LSP server in meta → CALLS edges weren't populated."""
    meta = ProjectMeta(
        project_slug="x",
        project_path="/tmp/x",
        lsp_server=None,
    )

    coverage = Coverage.from_meta(meta)

    assert coverage.calls_indexed is False
    assert coverage.lsp_server is None


def test_from_meta_with_none_returns_all_defaults() -> None:
    """Missing meta.json (no index yet) → all-defaults coverage."""
    coverage = Coverage.from_meta(None)

    assert coverage.calls_indexed is False
    assert coverage.lsp_server is None
    assert coverage.embedder_id is None
    assert coverage.indexed_at is None
    assert coverage.project_path is None


def test_coverage_is_frozen() -> None:
    """Coverage is a frozen Pydantic model — agents get an immutable snapshot."""
    coverage = Coverage(calls_indexed=True, lsp_server="x")

    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        coverage.calls_indexed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# semantic_search_available probe
# ---------------------------------------------------------------------------


class _LiveStubEmbedder:
    """Stub embedder whose ``embed`` returns a 1-element vector."""

    model_id = "stub:live"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0, 0.0, 0.0, 0.0] for _ in texts]


class _DeadStubEmbedder:
    """Stub embedder that raises on every ``embed`` call (daemon down)."""

    model_id = "stub:dead"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ConnectionError("simulated: cannot reach embedder daemon")


def test_semantic_search_available_is_none_when_no_embedder() -> None:
    """No embedder → field stays None (probing makes no sense)."""
    coverage = Coverage.from_meta(None)
    assert coverage.semantic_search_available is None


def test_semantic_search_available_true_when_live() -> None:
    """Healthy embedder → field becomes True."""
    coverage = Coverage.from_meta(None, embedder=_LiveStubEmbedder())  # type: ignore[arg-type]
    assert coverage.semantic_search_available is True


def test_semantic_search_available_false_when_dead() -> None:
    """Embedder that raises → field becomes False; no exception escapes."""
    coverage = Coverage.from_meta(None, embedder=_DeadStubEmbedder())  # type: ignore[arg-type]
    assert coverage.semantic_search_available is False


def test_probe_runs_exactly_once_per_coverage_construction() -> None:
    """Probe is a one-shot, not a per-tool-call cost."""
    call_count = 0

    class _CountingEmbedder:
        model_id = "stub:counter"
        dimensions = 4

        def embed(self, texts: list[str]) -> list[list[float]]:
            nonlocal call_count
            call_count += 1
            return [[0.0] * 4 for _ in texts]

    Coverage.from_meta(None, embedder=_CountingEmbedder())  # type: ignore[arg-type]
    assert call_count == 1
