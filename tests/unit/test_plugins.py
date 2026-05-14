"""Tests for nexus.plugins."""

from __future__ import annotations

from typing import Any

import pytest
from nexus.plugins import PluginRegistry, load_plugins
from nexus.plugins.loader import PluginLoadError

# ---------------------------------------------------------------------------
# PluginRegistry
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Minimal stub satisfying the Embedder protocol structurally."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    @property
    def model_id(self) -> str:
        return "fake:test"

    @property
    def dimensions(self) -> int:
        return 4

    def embed(self, texts) -> list[list[float]]:
        return [[0.0] * 4 for _ in texts]

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4


class FakeVectorStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def upsert(self, items) -> None:
        pass

    def delete(self, ids) -> None:
        pass

    def search(self, query, *, top_k):
        return []

    def count(self) -> int:
        return 0

    def iter_records(self):
        return iter([])

    def close(self) -> None:
        pass


def make_embedder(config: dict[str, Any]) -> FakeEmbedder:
    return FakeEmbedder(config)


def make_store(config: dict[str, Any]) -> FakeVectorStore:
    return FakeVectorStore(config)


class TestRegisterEmbedder:
    def test_register_and_resolve(self) -> None:
        registry = PluginRegistry()
        registry.register_embedder(
            name="fake",
            factory=make_embedder,
            source="test-suite",
            description="Fake embedder",
        )

        instance = registry.resolve_embedder("fake", {"api_key": "x"})
        assert isinstance(instance, FakeEmbedder)
        assert instance.config == {"api_key": "x"}

    def test_duplicate_name_is_rejected(self) -> None:
        registry = PluginRegistry()
        registry.register_embedder(
            name="fake",
            factory=make_embedder,
            source="plugin-a",
        )

        with pytest.raises(ValueError, match="already registered"):
            registry.register_embedder(
                name="fake",
                factory=make_embedder,
                source="plugin-b",
            )

    def test_resolve_unknown_name_raises(self) -> None:
        registry = PluginRegistry()
        with pytest.raises(KeyError, match="No embedder registered"):
            registry.resolve_embedder("missing", {})

    def test_names_are_sorted(self) -> None:
        registry = PluginRegistry()
        for name in ("zulu", "alpha", "mike"):
            registry.register_embedder(name=name, factory=make_embedder, source="test")
        assert registry.embedder_names() == ["alpha", "mike", "zulu"]


class TestRegisterVectorStore:
    def test_register_and_resolve(self) -> None:
        registry = PluginRegistry()
        registry.register_vector_store(
            name="fake-store",
            factory=make_store,
            source="test-suite",
        )

        instance = registry.resolve_vector_store("fake-store", {})
        assert isinstance(instance, FakeVectorStore)

    def test_duplicate_rejected(self) -> None:
        registry = PluginRegistry()
        registry.register_vector_store(name="s", factory=make_store, source="a")
        with pytest.raises(ValueError, match="already registered"):
            registry.register_vector_store(name="s", factory=make_store, source="b")


class TestDescribe:
    def test_describe_returns_serialisable_snapshot(self) -> None:
        registry = PluginRegistry()
        registry.register_embedder(
            name="fake",
            factory=make_embedder,
            source="test",
            description="Fake",
        )
        registry.register_vector_store(
            name="fake-store",
            factory=make_store,
            source="test",
            description="Fake store",
        )

        snapshot = registry.describe()

        assert snapshot == {
            "embedders": [{"name": "fake", "source": "test", "description": "Fake"}],
            "vector_stores": [
                {"name": "fake-store", "source": "test", "description": "Fake store"},
            ],
        }


# ---------------------------------------------------------------------------
# Plugin loader
# ---------------------------------------------------------------------------


class TestLoadPluginsEmpty:
    def test_loading_empty_group_returns_empty_result(self) -> None:
        registry = PluginRegistry()
        result = load_plugins(registry, entry_point_group="nexus.plugins.nonexistent")

        assert result.loaded == ()
        assert result.failed == {}


class TestLoadPluginsWithSyntheticEntryPoint:
    """Tests the loader by synthesising an EntryPoint in-memory.

    The real entry-point machinery reads package metadata, which we
    can't easily inject in a unit test. Instead we monkeypatch
    ``importlib.metadata.entry_points`` to return a hand-crafted list.
    """

    def test_successful_plugin_registers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A real module + function we can point the entry point at.
        def fake_register(registry: PluginRegistry) -> None:
            registry.register_embedder(
                name="synth",
                factory=make_embedder,
                source="synthetic-plugin",
            )

        import sys
        import types

        module = types.ModuleType("synthetic_nexus_plugin")
        module.register = fake_register  # type: ignore[attr-defined]
        sys.modules["synthetic_nexus_plugin"] = module

        from importlib.metadata import EntryPoint

        ep = EntryPoint(
            name="synth",
            value="synthetic_nexus_plugin:register",
            group="nexus.plugins.test",
        )

        from nexus.plugins import loader as loader_module

        def fake_entry_points(*, group: str):
            if group == "nexus.plugins.test":
                return [ep]
            return []

        monkeypatch.setattr(loader_module, "entry_points", fake_entry_points)

        registry = PluginRegistry()
        result = load_plugins(registry, entry_point_group="nexus.plugins.test")

        assert "synth" in result.loaded
        assert "synth" in registry.embedder_names()

    def test_plugin_exception_is_captured_in_non_strict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def bad_register(registry: PluginRegistry) -> None:
            raise RuntimeError("plugin exploded")

        import sys
        import types

        module = types.ModuleType("bad_nexus_plugin")
        module.register = bad_register  # type: ignore[attr-defined]
        sys.modules["bad_nexus_plugin"] = module

        from importlib.metadata import EntryPoint

        ep = EntryPoint(
            name="bad",
            value="bad_nexus_plugin:register",
            group="nexus.plugins.test",
        )

        from nexus.plugins import loader as loader_module

        def fake_entry_points(*, group: str):
            return [ep] if group == "nexus.plugins.test" else []

        monkeypatch.setattr(loader_module, "entry_points", fake_entry_points)

        registry = PluginRegistry()
        result = load_plugins(registry, entry_point_group="nexus.plugins.test")

        assert result.loaded == ()
        assert "bad" in result.failed
        assert "plugin exploded" in result.failed["bad"]

    def test_plugin_exception_reraised_in_strict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def bad_register(registry: PluginRegistry) -> None:
            raise RuntimeError("fatal")

        import sys
        import types

        module = types.ModuleType("strict_bad_plugin")
        module.register = bad_register  # type: ignore[attr-defined]
        sys.modules["strict_bad_plugin"] = module

        from importlib.metadata import EntryPoint

        ep = EntryPoint(
            name="strict-bad",
            value="strict_bad_plugin:register",
            group="nexus.plugins.test",
        )

        from nexus.plugins import loader as loader_module

        def fake_entry_points(*, group: str):
            return [ep] if group == "nexus.plugins.test" else []

        monkeypatch.setattr(loader_module, "entry_points", fake_entry_points)

        registry = PluginRegistry()
        with pytest.raises(PluginLoadError, match="strict-bad"):
            load_plugins(
                registry,
                strict=True,
                entry_point_group="nexus.plugins.test",
            )
