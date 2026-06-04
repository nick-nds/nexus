"""Unit tests for :func:`nexus.interfaces.cli.embedder.preflight_embedder`.

The embedder backends import their optional client package lazily (e.g.
``from ollama import Client`` inside ``OllamaEmbedder._get_client``), so a
missing ``nexus-php[ollama]`` install only blows up on the first ``embed``
call - which is the *last* indexing pass, after extraction and an 8-minute
LSP enrichment. The pre-flight probes the embedder up front so the run
fails in under a second with an actionable message instead.
"""

from __future__ import annotations

from nexus.adapters.embedders.errors import (
    EmbedderConnectionError,
    EmbedderModelNotFoundError,
)
from nexus.interfaces.cli.embedder import preflight_embedder


class _ReadyEmbedder:
    model_id = "ollama:nomic-embed-text"
    dimensions = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]


class _MissingPackageEmbedder:
    """Embedder whose lazy backend import fails (extra not installed)."""

    model_id = "ollama:nomic-embed-text"
    dimensions = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ModuleNotFoundError("No module named 'ollama'")


class _MissingFastembedEmbedder:
    model_id = "fastembed:BAAI/bge-small-en-v1.5"
    dimensions = 384

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise ModuleNotFoundError("No module named 'fastembed'")


class _DaemonDownEmbedder:
    model_id = "ollama:nomic-embed-text"
    dimensions = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbedderConnectionError("cannot reach http://localhost:11434")


class _ModelMissingEmbedder:
    model_id = "ollama:nomic-embed-text"
    dimensions = 768

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise EmbedderModelNotFoundError("model 'nomic-embed-text' not found")


def test_ready_embedder_returns_none() -> None:
    assert preflight_embedder(_ReadyEmbedder()) is None  # type: ignore[arg-type]


def test_missing_backend_package_names_the_install_extra() -> None:
    msg = preflight_embedder(_MissingPackageEmbedder())  # type: ignore[arg-type]
    assert msg is not None
    assert "nexus-php[ollama]" in msg


def test_missing_fastembed_maps_to_local_embeddings_extra() -> None:
    # The fastembed backend ships in the [local-embeddings] extra, not a
    # [fastembed] one - the hint must use the real extra name.
    msg = preflight_embedder(_MissingFastembedEmbedder())  # type: ignore[arg-type]
    assert msg is not None
    assert "nexus-php[local-embeddings]" in msg


def test_unreachable_daemon_is_reported() -> None:
    msg = preflight_embedder(_DaemonDownEmbedder())  # type: ignore[arg-type]
    assert msg is not None
    assert "unreachable" in msg.lower()
    assert "localhost:11434" in msg


def test_missing_model_is_reported() -> None:
    msg = preflight_embedder(_ModelMissingEmbedder())  # type: ignore[arg-type]
    assert msg is not None
    assert "model" in msg.lower()
