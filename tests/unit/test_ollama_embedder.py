"""Unit tests for the Ollama embedder adapter.

Tests use a stub Ollama client so they don't need a running daemon.
The one integration-level assertion (real daemon round-trip) lives
under ``tests/integration/`` and is gated on ``NEXUS_RUN_OLLAMA=1``
so CI can skip it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from nexus.adapters.embedders import (
    EmbedderConnectionError,
    EmbedderModelNotFoundError,
    EmbedderRequestError,
    OllamaEmbedder,
)

# ---------------------------------------------------------------------------
# Stub client
# ---------------------------------------------------------------------------


@dataclass
class _StubResponse:
    embeddings: list[list[float]]


class _StubClient:
    """Minimal stub of ``ollama.Client``.

    Records each ``embed`` call so tests can assert on arguments,
    and returns either a canned response or raises a configured
    exception.
    """

    def __init__(
        self,
        *,
        response: list[list[float]] | None = None,
        raise_on_embed: Exception | None = None,
    ) -> None:
        self._response = response
        self._raise = raise_on_embed
        self.calls: list[dict[str, Any]] = []

    def embed(
        self, *, model: str, input: list[str], options: dict[str, Any] | None = None
    ) -> _StubResponse:
        self.calls.append({"model": model, "input": list(input), "options": options})
        if self._raise is not None:
            raise self._raise
        if self._response is None:
            # Default: one 4-dim zero vector per input
            return _StubResponse(embeddings=[[0.0, 0.0, 0.0, 0.0] for _ in input])
        return _StubResponse(embeddings=self._response)


def _embedder_with_stub(stub: _StubClient, **kwargs: Any) -> OllamaEmbedder:
    emb = OllamaEmbedder(**kwargs)
    emb._client = stub  # type: ignore[attr-defined]
    return emb


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_model_id_is_stable(self) -> None:
        emb = OllamaEmbedder(model="nomic-embed-text")
        assert emb.model_id == "ollama:nomic-embed-text"

    def test_empty_input_returns_empty_without_calling_daemon(self) -> None:
        stub = _StubClient()
        emb = _embedder_with_stub(stub, model="m")

        result = emb.embed([])

        assert result == []
        assert stub.calls == []  # daemon not contacted

    def test_passes_texts_to_client_unchanged(self) -> None:
        stub = _StubClient()
        emb = _embedder_with_stub(stub, model="nomic-embed-text")

        emb.embed(["hello", "world"])

        assert len(stub.calls) == 1
        assert stub.calls[0]["model"] == "nomic-embed-text"
        assert stub.calls[0]["input"] == ["hello", "world"]

    def test_vectors_are_coerced_to_plain_lists(self) -> None:
        # Ollama's library returns vectors that might be numpy arrays;
        # the adapter should unconditionally give us plain lists of floats.
        stub = _StubClient(response=[[1.5, 2.5, 3.5]])
        emb = _embedder_with_stub(stub, model="m")

        result = emb.embed(["x"])

        assert result == [[1.5, 2.5, 3.5]]
        assert isinstance(result[0][0], float)

    def test_dimensions_updates_on_first_call(self) -> None:
        # Constructor default is 768 but the actual model returns 512.
        # The adapter should trust the live response.
        stub = _StubClient(response=[[0.0] * 512])
        emb = _embedder_with_stub(stub, model="m", dimensions=768)
        assert emb.dimensions == 768

        emb.embed(["x"])

        assert emb.dimensions == 512


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    def test_connection_refused_becomes_typed_error(self) -> None:
        stub = _StubClient(raise_on_embed=ConnectionError("Connection refused"))
        emb = _embedder_with_stub(stub, host="http://localhost:11434", model="m")

        with pytest.raises(EmbedderConnectionError, match="Start the daemon"):
            emb.embed(["x"])

    def test_unknown_model_becomes_typed_error(self) -> None:
        stub = _StubClient(raise_on_embed=RuntimeError("model 'nope' not found"))
        emb = _embedder_with_stub(stub, model="nope")

        with pytest.raises(EmbedderModelNotFoundError, match="ollama pull nope"):
            emb.embed(["x"])

    def test_generic_failure_becomes_request_error(self) -> None:
        stub = _StubClient(raise_on_embed=RuntimeError("server panic"))
        emb = _embedder_with_stub(stub, model="m")

        with pytest.raises(EmbedderRequestError, match="server panic"):
            emb.embed(["x"])

    def test_missing_embeddings_field_raises(self) -> None:
        class _BadClient:
            def embed(self, **kwargs: Any) -> object:
                class _X:
                    pass

                return _X()

        emb = OllamaEmbedder(model="m")
        emb._client = _BadClient()  # type: ignore[attr-defined]

        with pytest.raises(EmbedderRequestError, match="missing the 'embeddings'"):
            emb.embed(["x"])


# ---------------------------------------------------------------------------
# Truncation (workaround for Ollama <0.14 batch panic)
# ---------------------------------------------------------------------------


class TestTruncation:
    def test_short_inputs_are_unchanged(self) -> None:
        stub = _StubClient()
        emb = _embedder_with_stub(stub, model="m", max_input_chars=100)

        emb.embed(["short"])

        assert stub.calls[0]["input"] == ["short"]

    def test_long_inputs_are_truncated(self) -> None:
        stub = _StubClient()
        emb = _embedder_with_stub(stub, model="m", max_input_chars=50)

        emb.embed(["x" * 200])

        sent = stub.calls[0]["input"][0]
        assert len(sent) == 50

    def test_mixed_batch_only_truncates_long(self) -> None:
        stub = _StubClient()
        emb = _embedder_with_stub(stub, model="m", max_input_chars=10)

        emb.embed(["hi", "x" * 50, "ok"])

        sent = stub.calls[0]["input"]
        assert sent == ["hi", "x" * 10, "ok"]


# ---------------------------------------------------------------------------
# Cost / estimate_tokens
# ---------------------------------------------------------------------------


class TestEstimateTokens:
    def test_empty_string_is_one_token(self) -> None:
        assert OllamaEmbedder(model="m").estimate_tokens("") == 1

    def test_long_string_approximates_chars_over_four(self) -> None:
        text = "a" * 400
        assert OllamaEmbedder(model="m").estimate_tokens(text) == 100


# ---------------------------------------------------------------------------
# Host configuration
# ---------------------------------------------------------------------------


class TestHostConfig:
    def test_default_host(self) -> None:
        emb = OllamaEmbedder(model="m")
        assert emb._host == "http://localhost:11434"  # type: ignore[attr-defined]

    def test_env_var_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://remote:11434")
        emb = OllamaEmbedder(model="m")
        assert emb._host == "http://remote:11434"  # type: ignore[attr-defined]

    def test_explicit_host_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OLLAMA_HOST", "http://remote:11434")
        emb = OllamaEmbedder(model="m", host="http://override:9999")
        assert emb._host == "http://override:9999"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered_in_builtin(self) -> None:
        from nexus.adapters.embedders import register_builtin_embedders
        from nexus.plugins import PluginRegistry

        reg = PluginRegistry()
        register_builtin_embedders(reg)

        assert "ollama" in reg.embedder_names()
        assert "fastembed" in reg.embedder_names()

    def test_factory_constructs_embedder_with_defaults(self) -> None:
        from nexus.adapters.embedders import register_builtin_embedders
        from nexus.plugins import PluginRegistry

        reg = PluginRegistry()
        register_builtin_embedders(reg)
        emb = reg.resolve_embedder("ollama", {})

        assert emb.model_id == "ollama:nomic-embed-text"
        assert emb.dimensions == 768

    def test_factory_honours_config_overrides(self) -> None:
        from nexus.adapters.embedders import register_builtin_embedders
        from nexus.plugins import PluginRegistry

        reg = PluginRegistry()
        register_builtin_embedders(reg)
        emb = reg.resolve_embedder(
            "ollama",
            {
                "model": "mxbai-embed-large",
                "host": "http://localhost:9999",
                "dimensions": 1024,
                "timeout_seconds": 60,
            },
        )

        assert emb.model_id == "ollama:mxbai-embed-large"
        assert emb.dimensions == 1024
