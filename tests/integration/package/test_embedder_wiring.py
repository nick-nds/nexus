"""Embedder wiring: PackageIndexer writes vectors when an embedder is injected.

Pins down the fix for "package indexer skips embedding pass" (issue
discovered on the acme-platform run). Without the wiring, the
package indexer silently degraded to graph-only and wrote
``meta.embedder_id: null`` regardless of caller intent.

The test injects a deterministic stub embedder so it can run inside the
integration suite without needing Ollama / fastembed installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nexus.adapters.package.composer_metadata import read_composer_metadata
from nexus.pipeline.package_indexer import PackageIndexer
from tests.integration.package.conftest import EXTRACTOR_ROOT, skip_unless_integration

pytestmark = pytest.mark.integration


class _StubEmbedder:
    """Deterministic 4-dim embedder used to prove the pipeline wires it through."""

    model_id = "stub:wiring-probe"
    dimensions = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Hash-based but deterministic: each text maps to a stable vector.
        out: list[list[float]] = []
        for t in texts:
            h = hash(t)
            out.append([(h & 0xFF) / 255.0, ((h >> 8) & 0xFF) / 255.0, 0.5, 0.5])
        return out


@skip_unless_integration
def test_embedder_id_recorded_when_embedder_injected(fixture_clone: Path, tmp_path: Path) -> None:
    """Injecting an embedder populates ``meta.embedder_id`` (was always None before)."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
        embedder=_StubEmbedder(),  # type: ignore[arg-type]
    )

    meta_in = read_composer_metadata(fixture_clone)
    result = indexer.index(meta_in)

    persisted = json.loads((result.project_dir / "meta.json").read_text())
    assert persisted["embedder_id"] == "stub:wiring-probe"


@skip_unless_integration
def test_vectors_written_when_embedder_injected(fixture_clone: Path, tmp_path: Path) -> None:
    """LanceDB vectors directory is materialised when an embedder runs."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
        embedder=_StubEmbedder(),  # type: ignore[arg-type]
    )

    meta_in = read_composer_metadata(fixture_clone)
    result = indexer.index(meta_in)

    vectors_dir = result.project_dir / "vectors"
    assert vectors_dir.is_dir(), "vectors/ directory must exist after embedded indexing"


@skip_unless_integration
def test_embedder_id_is_none_when_embedder_omitted(fixture_clone: Path, tmp_path: Path) -> None:
    """Backward compatibility: omitting embedder still produces a graph-only index."""
    indexer = PackageIndexer(
        cache_root=tmp_path / "cache",
        nexus_root=tmp_path / "nexus",
        extractor_root=EXTRACTOR_ROOT,
    )

    meta_in = read_composer_metadata(fixture_clone)
    result = indexer.index(meta_in)

    persisted = json.loads((result.project_dir / "meta.json").read_text())
    assert persisted["embedder_id"] is None
    assert not (result.project_dir / "vectors").is_dir()
