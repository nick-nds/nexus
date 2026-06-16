"""``indexing.embed_batch_size`` wiring: nexus.yml -> pipeline embed pass.

Lowering the embed batch size lets CPU-only users keep each embedder
request under the timeout. The knob lives in ``nexus.yml`` and must flow
through to :class:`EmbedAndPersistPass`; it must never change chunking.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.config.project_profile import IndexingSettings
from nexus.interfaces.cli.commands._index_helpers import _project_embed_batch_size
from nexus.pipeline.factory import build_default_pipeline
from nexus.pipeline.passes import EmbedAndPersistPass

_DEFAULT_BATCH_SIZE = 256


def _embed_pass(pipeline: object) -> EmbedAndPersistPass:
    passes = pipeline.passes  # type: ignore[attr-defined]
    embed = next(p for p in passes if isinstance(p, EmbedAndPersistPass))
    return embed


class TestFactoryWiring:
    def test_batch_size_reaches_embed_pass(self) -> None:
        pipeline = build_default_pipeline(batch_size=64)

        assert _embed_pass(pipeline)._batch_size == 64  # type: ignore[attr-defined]

    def test_none_uses_pass_default(self) -> None:
        pipeline = build_default_pipeline(batch_size=None)

        assert _embed_pass(pipeline)._batch_size == _DEFAULT_BATCH_SIZE  # type: ignore[attr-defined]


class TestProjectBatchSizeReader:
    def test_reads_override_from_nexus_yml(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text(
            "schema_version: '1.0'\nproject:\n  slug: demo\nindexing:\n  embed_batch_size: 64\n"
        )

        assert _project_embed_batch_size(tmp_path) == 64

    def test_none_when_no_nexus_yml(self, tmp_path: Path) -> None:
        assert _project_embed_batch_size(tmp_path) is None

    def test_none_when_no_override(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text("schema_version: '1.0'\nproject:\n  slug: demo\n")

        assert _project_embed_batch_size(tmp_path) is None

    def test_none_when_malformed(self, tmp_path: Path) -> None:
        (tmp_path / "nexus.yml").write_text("{ not: valid: yaml :")

        assert _project_embed_batch_size(tmp_path) is None


class TestValidation:
    def test_zero_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="embed_batch_size"):
            IndexingSettings(embed_batch_size=0)

    def test_negative_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="embed_batch_size"):
            IndexingSettings(embed_batch_size=-1)
