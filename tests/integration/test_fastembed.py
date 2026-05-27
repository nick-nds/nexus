"""Integration tests for the fastembed backend.

These tests download a small ONNX model on first run (~150 MB). In
CI they are skipped by default - set ``NEXUS_RUN_FASTEMBED=1`` to
opt in. The tests verify that the adapter actually produces vectors
of the right shape for the embedding cache and vector store to use.
"""

from __future__ import annotations

import os

import pytest
from nexus.adapters.embedders import FastembedEmbedder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("NEXUS_RUN_FASTEMBED") != "1",
        reason="fastembed downloads a model; set NEXUS_RUN_FASTEMBED=1 to run",
    ),
]


@pytest.fixture(scope="module")
def embedder() -> FastembedEmbedder:
    return FastembedEmbedder(model="BAAI/bge-small-en-v1.5")


def test_model_id_is_stable(embedder: FastembedEmbedder) -> None:
    assert embedder.model_id == "fastembed:BAAI/bge-small-en-v1.5"


def test_dimensions_match_model(embedder: FastembedEmbedder) -> None:
    vectors = embedder.embed(["hello world"])
    assert len(vectors) == 1
    assert len(vectors[0]) == embedder.dimensions


def test_batch_embed(embedder: FastembedEmbedder) -> None:
    texts = ["foo", "bar", "baz"]
    vectors = embedder.embed(texts)
    assert len(vectors) == len(texts)


def test_empty_batch_returns_empty(embedder: FastembedEmbedder) -> None:
    assert embedder.embed([]) == []


def test_deterministic(embedder: FastembedEmbedder) -> None:
    first = embedder.embed(["deterministic"])
    second = embedder.embed(["deterministic"])
    assert first == second


def test_estimate_tokens_is_approximate() -> None:
    emb = FastembedEmbedder()
    assert emb.estimate_tokens("") == 1  # floor enforced
    assert emb.estimate_tokens("a" * 400) >= 90  # ~4 chars/token
