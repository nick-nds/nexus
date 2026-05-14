"""Integration tests for the LanceDB vector store."""

from __future__ import annotations

from pathlib import Path

import pytest
from nexus.adapters.storage import (
    LanceDbVectorStore,
    LanceVectorRecord,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "vectors"
    s = LanceDbVectorStore(path, dimensions=4)
    yield s
    s.close()


def vec(*values: float) -> list[float]:
    return list(values)


class TestUpsertAndCount:
    def test_empty_store_has_zero_count(self, store: LanceDbVectorStore) -> None:
        assert store.count() == 0

    def test_upsert_adds_rows(self, store: LanceDbVectorStore) -> None:
        store.upsert(
            [
                LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={"src": "A.php"}),
                LanceVectorRecord(id="b", vector=vec(0, 1, 0, 0), payload={"src": "B.php"}),
            ],
        )
        assert store.count() == 2

    def test_upsert_empty_list_is_noop(self, store: LanceDbVectorStore) -> None:
        store.upsert([])
        assert store.count() == 0

    def test_upsert_replaces_existing_ids(self, store: LanceDbVectorStore) -> None:
        store.upsert([LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={"v": 1})])
        store.upsert([LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={"v": 2})])

        assert store.count() == 1
        hits = store.search(vec(1, 0, 0, 0), top_k=1)
        assert hits[0].payload == {"v": 2}


class TestSearch:
    def test_search_returns_nearest_first(self, store: LanceDbVectorStore) -> None:
        store.upsert(
            [
                LanceVectorRecord(id="x", vector=vec(1, 0, 0, 0), payload={}),
                LanceVectorRecord(id="y", vector=vec(0, 1, 0, 0), payload={}),
                LanceVectorRecord(id="z", vector=vec(0, 0, 1, 0), payload={}),
            ],
        )

        hits = store.search(vec(1, 0, 0, 0), top_k=3)

        assert len(hits) == 3
        assert hits[0].id == "x"
        # Score is in [0, 1] range (1 - cosine_distance).
        assert 0.0 <= hits[0].score <= 1.0

    def test_search_on_empty_store_returns_empty(self, store: LanceDbVectorStore) -> None:
        assert store.search(vec(1, 0, 0, 0), top_k=5) == []

    def test_search_respects_top_k(self, store: LanceDbVectorStore) -> None:
        store.upsert(
            [
                LanceVectorRecord(id=f"v{i}", vector=vec(float(i), 0, 0, 0), payload={})
                for i in range(5)
            ],
        )
        hits = store.search(vec(2.5, 0, 0, 0), top_k=2)
        assert len(hits) == 2

    def test_payload_round_trips_through_json(self, store: LanceDbVectorStore) -> None:
        payload = {"src": "App.php", "line": 42, "kind": "method", "meta": {"x": [1, 2]}}
        store.upsert([LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload=payload)])

        hits = store.search(vec(1, 0, 0, 0), top_k=1)

        assert hits[0].payload == payload


class TestDelete:
    def test_delete_removes_rows(self, store: LanceDbVectorStore) -> None:
        store.upsert(
            [
                LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={}),
                LanceVectorRecord(id="b", vector=vec(0, 1, 0, 0), payload={}),
                LanceVectorRecord(id="c", vector=vec(0, 0, 1, 0), payload={}),
            ],
        )
        store.delete(["a", "c"])

        assert store.count() == 1
        hits = store.search(vec(0, 1, 0, 0), top_k=5)
        assert len(hits) == 1
        assert hits[0].id == "b"

    def test_delete_missing_ids_is_noop(self, store: LanceDbVectorStore) -> None:
        store.upsert([LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={})])
        store.delete(["never-existed"])
        assert store.count() == 1

    def test_delete_empty_list_is_noop(self, store: LanceDbVectorStore) -> None:
        store.delete([])
        assert store.count() == 0


class TestPersistence:
    def test_store_survives_reopen(self, tmp_path: Path) -> None:
        path = tmp_path / "vectors"
        store1 = LanceDbVectorStore(path, dimensions=4)
        store1.upsert([LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={"x": 1})])
        store1.close()

        store2 = LanceDbVectorStore(path, dimensions=4)
        assert store2.count() == 1
        hits = store2.search(vec(1, 0, 0, 0), top_k=1)
        assert hits[0].id == "a"
        assert hits[0].payload == {"x": 1}
        store2.close()


class TestIterRecords:
    def test_iter_on_empty_store_yields_nothing(self, store: LanceDbVectorStore) -> None:
        assert list(store.iter_records()) == []

    def test_iter_yields_every_upserted_row(self, store: LanceDbVectorStore) -> None:
        store.upsert(
            [
                LanceVectorRecord(id="a", vector=vec(1, 0, 0, 0), payload={"k": "first"}),
                LanceVectorRecord(id="b", vector=vec(0, 1, 0, 0), payload={"k": "second"}),
                LanceVectorRecord(id="c", vector=vec(0, 0, 1, 0), payload={"k": "third"}),
            ]
        )

        records = list(store.iter_records())

        # Order is unspecified by the protocol — sort to compare.
        records.sort(key=lambda r: r.id)
        assert [r.id for r in records] == ["a", "b", "c"]
        payloads = {r.id: r.payload for r in records}
        assert payloads["a"] == {"k": "first"}
        assert payloads["b"] == {"k": "second"}
        assert payloads["c"] == {"k": "third"}

    def test_iter_yields_payload_dicts_not_raw_strings(
        self,
        store: LanceDbVectorStore,
    ) -> None:
        """The payload comes back already JSON-decoded; callers don't need to."""
        store.upsert(
            [
                LanceVectorRecord(
                    id="x",
                    vector=vec(1, 0, 0, 0),
                    payload={"node_id": "method:Foo::bar", "start_line": 10},
                ),
            ],
        )

        record = next(iter(store.iter_records()))

        assert isinstance(record.payload, dict)
        assert record.payload["node_id"] == "method:Foo::bar"
        assert record.payload["start_line"] == 10
