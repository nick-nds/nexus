"""Integration test for the Ollama embedder against a real daemon.

Gated on ``NEXUS_RUN_OLLAMA=1`` so CI skips it unless explicitly
opted in. Expects:

* ``ollama serve`` already running on the default port.
* The ``nomic-embed-text`` model already pulled (``ollama pull nomic-embed-text``).
"""

from __future__ import annotations

import os

import pytest
from nexus.adapters.embedders import OllamaEmbedder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("NEXUS_RUN_OLLAMA") != "1",
        reason="Requires a running Ollama daemon; set NEXUS_RUN_OLLAMA=1 to run",
    ),
]


@pytest.fixture
def embedder():
    emb = OllamaEmbedder(model="nomic-embed-text")
    yield emb
    emb.close()


def test_embed_single(embedder: OllamaEmbedder) -> None:
    vectors = embedder.embed(["hello world"])
    assert len(vectors) == 1
    assert len(vectors[0]) == embedder.dimensions > 0


def test_embed_batch(embedder: OllamaEmbedder) -> None:
    texts = [f"test chunk {i}" for i in range(8)]
    vectors = embedder.embed(texts)
    assert len(vectors) == 8
    assert all(len(v) == embedder.dimensions for v in vectors)


def test_empty_input_is_no_op(embedder: OllamaEmbedder) -> None:
    assert embedder.embed([]) == []


def test_deterministic_for_same_input(embedder: OllamaEmbedder) -> None:
    first = embedder.embed(["deterministic test"])
    second = embedder.embed(["deterministic test"])
    assert first == second
