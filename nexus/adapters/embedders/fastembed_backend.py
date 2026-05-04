"""Local ONNX-based embedder using :mod:`fastembed`.

Chosen over sentence-transformers because fastembed ships as an ONNX
runtime with a much smaller footprint (~100 MB total) and doesn't
require PyTorch or a CUDA install. The "local, free, no API key,
works out of the box" promise of the free tier is easier to keep with
fastembed as the default.

Models shipped with fastembed at the time of writing include
:code:`BAAI/bge-small-en-v1.5` (the default), :code:`jinaai/jina-embeddings-v2-base-code`
(a code-tuned model), and a handful of multilingual options. The
backend surfaces the model choice as constructor configuration so
callers — and the Phase 5 CLI — can swap models without touching
adapter internals.

Determinism
===========

fastembed models are deterministic for a given (model name, model
weights) pair. That lets the embedding cache key on a content hash
plus the backend's ``model_id`` without risk of silent cache misses
from non-deterministic output.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


# Default model. Chosen for:
# * Small download (~150 MB)
# * General-purpose English quality (BGE family)
# * Known dimensionality (384) so the vector store schema is
#   predictable for the default-install case.
_DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
_DEFAULT_DIMENSIONS = 384


class FastembedEmbedder:
    """Local ONNX embedder powered by :mod:`fastembed`.

    The model is loaded lazily on first call to :meth:`embed`. This
    keeps tests that merely *construct* an embedder fast (the model
    files are only fetched when needed) and lets the CLI print a
    "downloading model..." progress message before the first slow
    call.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        cache_dir: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        """Build a fastembed-backed embedder.

        Args:
            model: HuggingFace-style model name. See
                https://qdrant.github.io/fastembed/examples/Supported_Models/
                for the list of supported models.
            cache_dir: Directory fastembed will use for model weights.
                Defaults to the library's own cache location
                (``~/.cache/fastembed``). Override for tests or for
                users with a shared model cache.
            dimensions: Vector dimensionality override. Usually left
                at the default; the fastembed model metadata exposes
                the real dimensionality once the model is loaded.
        """
        self._model_name = model
        self._cache_dir = cache_dir
        self._dimensions = dimensions or _DEFAULT_DIMENSIONS
        self._model: Any | None = None

    @property
    def model_id(self) -> str:
        """Stable identifier for cache keying."""
        return f"fastembed:{self._model_name}"

    @property
    def dimensions(self) -> int:
        """Vector dimensionality produced by the backend."""
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of strings into vectors.

        Loads the model on first call. Every vector is length
        :attr:`dimensions`.
        """
        if not texts:
            return []

        model = self._load_model()
        # fastembed returns a generator of numpy arrays; materialise
        # them into plain float lists so the rest of Nexus doesn't
        # have to depend on numpy.
        outputs = list(model.embed(list(texts)))
        return [list(vec.tolist()) for vec in outputs]

    def estimate_tokens(self, text: str) -> int:
        """Approximate the token count for cost estimation.

        Local backends are free so the estimate is only used for
        observability. We use the cheap "~4 characters per token"
        heuristic common in LLM tooling; this over-counts for
        short code identifiers and under-counts for long docblocks
        but is within an order of magnitude of reality for any
        paid backend that might share the cost gate.
        """
        return max(1, len(text) // 4)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_model(self) -> Any:
        if self._model is None:
            # Deferred import so the library is not pulled in for
            # users that don't use fastembed (e.g. Ollama users).
            from fastembed import TextEmbedding  # noqa: PLC0415

            kwargs: dict[str, Any] = {"model_name": self._model_name}
            if self._cache_dir is not None:
                kwargs["cache_dir"] = self._cache_dir
            self._model = TextEmbedding(**kwargs)
        return self._model
