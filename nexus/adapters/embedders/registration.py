"""Wire built-in embedder backends into a :class:`PluginRegistry`.

Called once from the pipeline bootstrap before user plugins loaded
via entry points. A user plugin that wants to replace a built-in
backend can register the same name - the registry rejects
duplicates, so the pipeline's registration order is the one place
to tweak if that needs to change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from nexus.adapters.embedders.fastembed_backend import FastembedEmbedder
from nexus.adapters.embedders.ollama_backend import OllamaEmbedder

if TYPE_CHECKING:
    from nexus.core.protocols import Embedder
    from nexus.plugins.registry import PluginRegistry


def register_builtin_embedders(registry: PluginRegistry) -> None:
    """Register every built-in embedder factory on ``registry``.

    Registers ``fastembed`` (the zero-setup local default) and
    ``ollama`` (the fast local backend that uses a GPU when
    available). Each registration is independent; importing this
    module doesn't pull in an optional-extra backend unless its
    factory is actually invoked.
    """
    registry.register_embedder(
        name="fastembed",
        factory=_fastembed_factory,
        source="nexus.adapters.embedders",
        description=(
            "Local ONNX embedder (no API key, no daemon, no GPU). "
            "Default model: BAAI/bge-small-en-v1.5 (384-dim). "
            "~5 chunks/sec on CPU."
        ),
    )
    registry.register_embedder(
        name="ollama",
        factory=_ollama_factory,
        source="nexus.adapters.embedders",
        description=(
            "Ollama-backed embedder. Default model: nomic-embed-text "
            "(768-dim). Requires `ollama serve` running locally. "
            "~76 chunks/sec on CPU, faster on GPU. Recommended for any "
            "project larger than a few hundred classes."
        ),
    )


def _fastembed_factory(config: dict[str, Any]) -> Embedder:
    """Factory that constructs a :class:`FastembedEmbedder` from config."""
    model = str(config.get("model") or "BAAI/bge-small-en-v1.5")
    cache_dir = config.get("cache_dir")
    dimensions = config.get("dimensions")
    return FastembedEmbedder(
        model=model,
        cache_dir=str(cache_dir) if cache_dir is not None else None,
        dimensions=int(dimensions) if dimensions is not None else None,
    )


def _ollama_factory(config: dict[str, Any]) -> Embedder:
    """Factory that constructs an :class:`OllamaEmbedder` from config."""
    model = str(config.get("model") or "nomic-embed-text")
    host = config.get("host")
    dimensions = config.get("dimensions")
    timeout = config.get("timeout_seconds")
    return OllamaEmbedder(
        model=model,
        host=str(host) if host is not None else None,
        dimensions=int(dimensions) if dimensions is not None else None,
        timeout_seconds=float(timeout) if timeout is not None else 120.0,
    )
