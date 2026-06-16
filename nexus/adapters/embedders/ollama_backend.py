"""Ollama-backed embedder.

Ollama runs a small local HTTP daemon that exposes any GGUF-quantised
embedding model as an ``/api/embed`` endpoint. Unlike fastembed, it
can use a GPU if one is available (via llama.cpp's backends), and it
handles batch inference efficiently for text-length inputs typical
of code chunks.

On the validation hardware with an already-downloaded ``nomic-embed-text``
model, Ollama processes ~76 real chunks/sec - about **15x faster than
fastembed on CPU** on the same machine. For enterprise-scale projects
(20k+ chunks) that's the difference between a 4-minute embed phase
and a 67-minute one.

Why HTTP over embedding the model directly:

* Ollama abstracts model lifecycle (download, quantisation, GPU
  backend selection) and presents a stable REST API.
* Zero Python-side dependency on llama.cpp / PyTorch / CUDA wheels.
* The user can swap models via ``ollama pull`` without touching
  Nexus config.
* Other tools the user already has (Open WebUI, Claude Desktop
  integrations) share the same Ollama installation.

Trade-offs:

* The daemon must be running. We surface a clear error if it isn't.
* Network round-trips add small per-batch latency. Mitigated by
  batching ~256 chunks per request.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Any

from nexus.adapters.embedders.errors import (
    EmbedderConnectionError,
    EmbedderModelNotFoundError,
    EmbedderRequestError,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Default model. ``nomic-embed-text`` is a strong general-purpose
#: embedding model that also performs well on code. 768 dimensions,
#: ~274 MB on disk. Users can override per-project via ``nexus.yml``.
_DEFAULT_MODEL = "nomic-embed-text"

#: Dimensions of the default model. Used as a fallback when the
#: daemon is not yet running and we still need to construct a vector
#: store with a known width.
_DEFAULT_DIMENSIONS = 768

#: Maximum input length (characters) sent to Ollama per chunk.
#: Longer inputs are truncated before the request.
#:
#: Chosen as a safe upper bound under Ollama's single-batch ceiling
#: even for models with generous context windows. At roughly 4
#: characters per token this is ~1500 tokens, well under the 2048
#: token single-batch limit we set via ``num_batch`` below. The
#: enriched text template puts graph context at the top, so
#: truncating the tail of a long method body loses the least
#: relevant signal.
_DEFAULT_MAX_INPUT_CHARS = 6000

#: ``num_batch`` option passed to Ollama. Ollama's default of 512 is
#: too small for real code chunks - nomic-embed-text will panic with
#: ``"caching disabled but unable to fit entire input in a batch"``
#: on any input that tokenises to more than 512 tokens in one
#: request. Setting num_batch to 2048 avoids the panic while still
#: fitting in modest GPU memory budgets.
_DEFAULT_NUM_BATCH = 2048


class OllamaEmbedder:
    """Embedder that talks to a local Ollama HTTP daemon.

    Connection details are resolved on first use, not in
    ``__init__``, so an instance can be constructed without a running
    daemon. The daemon only needs to be up when :meth:`embed` runs.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_MODEL,
        host: str | None = None,
        dimensions: int | None = None,
        timeout_seconds: float = 300.0,
        max_input_chars: int | None = None,
    ) -> None:
        """Build an Ollama-backed embedder.

        Args:
            model: Model name as understood by the local Ollama
                daemon (e.g. ``nomic-embed-text``, ``mxbai-embed-large``).
                The model must already be pulled - Nexus does not
                fetch models on the user's behalf.
            host: Ollama daemon base URL. Defaults to the
                ``OLLAMA_HOST`` environment variable if set, else
                ``http://localhost:11434`` which matches Ollama's own
                default.
            dimensions: Vector width. The Ollama API doesn't expose
                a cheap "what size will this model produce" query, so
                we supply the default model's dimensions up front and
                let callers override for other models. If the first
                embed call returns a different size we trust that
                and update the stored value.
            timeout_seconds: Per-request timeout. Defaults to 5
                minutes. A GPU batch finishes in seconds; the generous
                default is headroom for CPU-only inference of large
                models, where a 256-chunk batch can take minutes. Raise
                it further (or lower ``embed_batch_size``) via config if
                a batch still exceeds it.
            max_input_chars: Per-input character limit before
                truncation. Defaults to
                :data:`_DEFAULT_MAX_INPUT_CHARS`. Ollama < 0.14 panics
                with ``"caching disabled but unable to fit entire
                input in a batch"`` on inputs that don't fit a single
                batch window; pre-truncating here avoids the server-
                side crash at the cost of losing the tail of unusually
                long methods.
        """
        self._model = model
        self._host = (
            host if host is not None else os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        )
        self._dimensions = dimensions if dimensions is not None else _DEFAULT_DIMENSIONS
        self._timeout = timeout_seconds
        self._max_input_chars = max_input_chars or _DEFAULT_MAX_INPUT_CHARS
        self._client: Any | None = None

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    @property
    def model_id(self) -> str:
        """Stable id used for embedding cache keys."""
        return f"ollama:{self._model}"

    @property
    def dimensions(self) -> int:
        """Vector width produced by the backend.

        May be the compile-time default until the first successful
        :meth:`embed` call confirms the actual model output width.
        """
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of strings into vectors.

        Args:
            texts: Sequence of strings. Empty sequences return an
                empty list without contacting the daemon.

        Returns:
            One vector per input, same order, each of length
            :attr:`dimensions`.

        Raises:
            EmbedderConnectionError: the daemon is not running or
                not reachable at the configured host.
            EmbedderModelNotFoundError: the daemon reports the model
                is not installed. Remediation: ``ollama pull <model>``.
            EmbedderRequestError: any other API-level failure.
        """
        text_list = [self._truncate(t) for t in texts]
        if not text_list:
            return []

        client = self._get_client()

        try:
            response = client.embed(
                model=self._model,
                input=text_list,
                options={"num_batch": _DEFAULT_NUM_BATCH},
            )
        except Exception as e:
            self._raise_user_friendly(e)

        vectors = self._extract_vectors(response)
        if vectors and len(vectors[0]) != self._dimensions:
            # Trust the live response over the constructor-time guess.
            self._dimensions = len(vectors[0])
        return vectors

    def estimate_tokens(self, text: str) -> int:
        """Approximate token count for cost reporting.

        Ollama is free so this exists only for the shared cost-gate
        interface. We use the same 4-chars-per-token heuristic as
        the fastembed backend.
        """
        return max(1, len(text) // 4)

    def close(self) -> None:
        """Release the underlying HTTP connection pool.

        The ollama Python client wraps an :mod:`httpx.Client` under
        the private ``_client`` attribute that holds persistent
        sockets. Leaving it un-closed at process exit fires a
        :class:`ResourceWarning`, which pytest's strict filter turns
        into a test failure. The pipeline calls this once at the end
        of a run; tests call it from teardown.

        Best-effort: if the installed ollama version restructures its
        internals, we still clear our own reference so the garbage
        collector can take it.
        """
        if self._client is None:
            return
        inner = getattr(self._client, "_client", None)
        if inner is not None and hasattr(inner, "close"):
            with contextlib.suppress(Exception):
                inner.close()
        self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _truncate(self, text: str) -> str:
        """Truncate an input text to :attr:`_max_input_chars` characters.

        The enriched text template (see
        :class:`nexus.core.chunking.EnrichedTextBuilder`) writes the
        header, file location, and graph context first, with the
        source body last. Truncating from the tail preserves the
        retrieval-critical information and only loses part of the
        method body.
        """
        if len(text) <= self._max_input_chars:
            return text
        return text[: self._max_input_chars]

    def _get_client(self) -> Any:
        if self._client is None:
            # Deferred import so Nexus can be installed without the
            # optional ``ollama`` dependency when users don't need it.
            from ollama import Client  # noqa: PLC0415

            self._client = Client(host=self._host, timeout=self._timeout)
        return self._client

    @staticmethod
    def _extract_vectors(response: Any) -> list[list[float]]:
        """Normalise the Ollama client's response shape into plain lists.

        The library returns a ``BaseModel``-like object with an
        ``embeddings`` attribute. We coerce to a nested Python list so
        the rest of Nexus doesn't see library-specific types.
        """
        raw = getattr(response, "embeddings", None)
        if raw is None and isinstance(response, dict):
            raw = response.get("embeddings")
        if raw is None:
            raise EmbedderRequestError(
                "Ollama response is missing the 'embeddings' field.",
            )
        return [[float(x) for x in vec] for vec in raw]

    def _raise_user_friendly(self, exc: Exception) -> None:
        """Translate ollama/httpx exceptions into Nexus-typed ones.

        The ollama Python client wraps ``httpx`` errors in its own
        ``ResponseError`` type for API failures and lets connect
        errors bubble up raw. We match on both so Phase 5's CLI can
        print a clean remediation message rather than a Python stack
        trace.
        """
        message = str(exc)
        lower = message.lower()

        if "connect" in lower or "connection refused" in lower or "nodename" in lower:
            raise EmbedderConnectionError(
                f"Cannot reach Ollama at {self._host}. "
                f"Start the daemon with `ollama serve` or set OLLAMA_HOST "
                f"to a reachable URL.",
            ) from exc

        if "model" in lower and ("not found" in lower or "does not exist" in lower):
            raise EmbedderModelNotFoundError(
                f"Ollama model {self._model!r} is not installed. "
                f"Run `ollama pull {self._model}` and retry.",
            ) from exc

        raise EmbedderRequestError(
            f"Ollama embed request failed: {message}",
        ) from exc
