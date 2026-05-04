"""Tests for nexus.adapters.embedders.cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nexus.adapters.embedders.cache import (
    EmbeddingCache,
    EmbeddingCacheError,
)


@pytest.fixture
def cache(tmp_path: Path) -> EmbeddingCache:
    return EmbeddingCache(root=tmp_path / "embeddings")


class TestBasicRoundTrip:
    def test_miss_returns_none(self, cache: EmbeddingCache) -> None:
        assert cache.get(model_id="fastembed:m", text="hello") is None

    def test_put_then_get(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="fastembed:m", text="hello", vector=[1.0, 2.0, 3.0])

        assert cache.get(model_id="fastembed:m", text="hello") == [1.0, 2.0, 3.0]

    def test_different_model_is_a_miss(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="fastembed:a", text="hello", vector=[1.0])

        assert cache.get(model_id="fastembed:b", text="hello") is None

    def test_different_text_is_a_miss(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="fastembed:m", text="one", vector=[1.0])

        assert cache.get(model_id="fastembed:m", text="two") is None


class TestBatch:
    def test_batch_split_hits_and_misses(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="m", text="hit", vector=[1.0])

        hits, misses = cache.get_batch(model_id="m", texts=["hit", "miss", "other"])

        assert hits == {"hit": [1.0]}
        assert misses == ["miss", "other"]

    def test_put_batch(self, cache: EmbeddingCache) -> None:
        cache.put_batch(
            model_id="m",
            pairs={"a": [1.0], "b": [2.0], "c": [3.0]},
        )

        assert cache.size() == 3
        assert cache.get(model_id="m", text="b") == [2.0]


class TestPersistence:
    def test_survives_fresh_instance(self, tmp_path: Path) -> None:
        root = tmp_path / "c"
        first = EmbeddingCache(root=root)
        first.put(model_id="m", text="hello", vector=[1.0, 2.0])

        second = EmbeddingCache(root=root)
        assert second.get(model_id="m", text="hello") == [1.0, 2.0]

    def test_atomic_write_leaves_no_tmp_file(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="m", text="x", vector=[1.0])

        tmps = list((cache.root).rglob("*.json.tmp"))
        assert tmps == []


class TestCorruption:
    def test_corrupt_file_is_treated_as_miss(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="m", text="hello", vector=[1.0])

        # Corrupt the file on disk.
        sub = cache.directory_for("m")
        entry = next(sub.glob("*.json"))
        entry.write_text("{ not valid json")

        assert cache.get(model_id="m", text="hello") is None

    def test_non_list_payload_is_treated_as_miss(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="m", text="hello", vector=[1.0])
        sub = cache.directory_for("m")
        entry = next(sub.glob("*.json"))
        entry.write_text(json.dumps({"not": "a list"}))

        assert cache.get(model_id="m", text="hello") is None


class TestClear:
    def test_clear_all(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="a", text="x", vector=[1.0])
        cache.put(model_id="b", text="y", vector=[2.0])

        deleted = cache.clear()

        assert deleted == 2
        assert cache.size() == 0

    def test_clear_single_model(self, cache: EmbeddingCache) -> None:
        cache.put(model_id="a", text="x", vector=[1.0])
        cache.put(model_id="b", text="y", vector=[2.0])

        cache.clear(model_id="a")

        assert cache.size() == 1
        assert cache.get(model_id="a", text="x") is None
        assert cache.get(model_id="b", text="y") == [2.0]


class TestSanitisation:
    def test_model_ids_with_slashes_work(self, cache: EmbeddingCache) -> None:
        model_id = "fastembed:BAAI/bge-small-en-v1.5"
        cache.put(model_id=model_id, text="x", vector=[1.0])

        assert cache.get(model_id=model_id, text="x") == [1.0]
        # The on-disk directory name should not contain slashes or colons.
        for sub in cache.root.iterdir():
            assert "/" not in sub.name
            assert ":" not in sub.name


class TestErrorHandling:
    def test_unwritable_root_raises(self) -> None:
        with pytest.raises(EmbeddingCacheError):
            EmbeddingCache(root=Path("/proc/nonexistent/nexus-cache"))
