"""Embedder backends implementing :class:`~nexus.core.protocols.Embedder`.

v1 ships two local backends:

* **fastembed** — ONNX-based, bundled with the ``[local-embeddings]``
  extra, no external daemon. Slow on CPU (~5 chunks/sec on real
  code) but zero setup.
* **Ollama** — HTTP client to a local Ollama daemon, bundled with
  the ``[ollama]`` extra. about 15x faster than fastembed on CPU and
  transparently uses a GPU if one is available. Recommended for
  any project larger than a few hundred classes.

OpenAI and Voyage backends land later as Phase 3.5 follow-ups.

The :func:`register_builtin_embedders` helper wires every built-in
backend into a :class:`~nexus.plugins.PluginRegistry`. The pipeline
calls this once at startup before plugins from entry-points are
loaded, so user plugins can override a built-in by registering the
same name (and taking responsibility for the duplicate).
"""

from nexus.adapters.embedders.cache import EmbeddingCache, EmbeddingCacheError
from nexus.adapters.embedders.errors import (
    EmbedderConnectionError,
    EmbedderError,
    EmbedderModelNotFoundError,
    EmbedderRequestError,
)
from nexus.adapters.embedders.fastembed_backend import FastembedEmbedder
from nexus.adapters.embedders.ollama_backend import OllamaEmbedder
from nexus.adapters.embedders.registration import register_builtin_embedders

__all__ = [
    "EmbedderConnectionError",
    "EmbedderError",
    "EmbedderModelNotFoundError",
    "EmbedderRequestError",
    "EmbeddingCache",
    "EmbeddingCacheError",
    "FastembedEmbedder",
    "OllamaEmbedder",
    "register_builtin_embedders",
]
