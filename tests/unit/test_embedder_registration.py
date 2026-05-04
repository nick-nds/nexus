"""Tests for nexus.adapters.embedders.registration."""

from __future__ import annotations

from nexus.adapters.embedders import register_builtin_embedders
from nexus.plugins import PluginRegistry


def test_registers_fastembed() -> None:
    registry = PluginRegistry()

    register_builtin_embedders(registry)

    assert "fastembed" in registry.embedder_names()


def test_factory_returns_embedder() -> None:
    registry = PluginRegistry()
    register_builtin_embedders(registry)

    # The factory runs; the returned object satisfies the Embedder
    # protocol structurally (model_id + dimensions + embed).
    embedder = registry.resolve_embedder("fastembed", {})

    assert embedder.model_id == "fastembed:BAAI/bge-small-en-v1.5"
    assert embedder.dimensions == 384


def test_factory_accepts_model_override() -> None:
    registry = PluginRegistry()
    register_builtin_embedders(registry)

    embedder = registry.resolve_embedder(
        "fastembed",
        {"model": "some/custom-model", "dimensions": 768},
    )

    assert embedder.model_id == "fastembed:some/custom-model"
    assert embedder.dimensions == 768
