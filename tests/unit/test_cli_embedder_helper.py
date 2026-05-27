"""Unit tests for :func:`build_embedder_from_config`.

The helper resolves an embedder from ``<storage_root>/config.yml`` and
is shared by ``CliContext._load_embedder`` (query path) and
``_index_helpers._build_embedder`` (indexing path). The behaviour these
tests pin down is:

* Missing ``config.yml`` → ``None`` (no embedder configured).
* Unknown provider → ``None`` (degrades gracefully).
* Known provider → constructed embedder instance.
"""

from __future__ import annotations

from pathlib import Path

from nexus.interfaces.cli.embedder import build_embedder_from_config


def test_returns_none_when_config_yml_is_missing(tmp_path: Path) -> None:
    assert build_embedder_from_config(tmp_path) is None


def test_returns_none_when_provider_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text(
        'schema_version: "1.0"\nembedder:\n  provider: nonexistent_provider\n  model: anything\n',
        encoding="utf-8",
    )

    assert build_embedder_from_config(tmp_path) is None


def test_returns_embedder_when_provider_is_known(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text(
        'schema_version: "1.0"\n'
        "embedder:\n"
        "  provider: fastembed\n"
        "  model: BAAI/bge-small-en-v1.5\n",
        encoding="utf-8",
    )

    embedder = build_embedder_from_config(tmp_path)

    assert embedder is not None
    assert embedder.model_id  # smoke check - every embedder has an id
