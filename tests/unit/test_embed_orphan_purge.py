"""Stale-vector purging on re-index.

``ChunkPass`` walks the whole reflection every run, so ``ctx.chunks`` is
always the complete set. The vector store, however, only ever *upserts*
the current ids - it has no full-replace. That left rows for chunks that
no longer exist (a renamed or deleted symbol mints a new chunk id)
permanently searchable: ``semantic_search`` would return a hit labelled
with a method that isn't in the code any more, and because the snippet is
re-read from the current file at the stored line range it looked
plausible. Re-indexing never fixed it because orphans were never removed.

The graph store is already a destination of record (it clears nodes and
edges on persist); this brings the vector store in line.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from nexus.pipeline.passes import EmbedAndPersistPass

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class _FakeVectorStore:
    """Minimal store exposing just what the purge touches."""

    def __init__(self, ids: list[str]) -> None:
        self._ids = list(ids)
        self.deleted: list[str] = []

    def iter_records(self) -> Iterator[SimpleNamespace]:
        for chunk_id in self._ids:
            yield SimpleNamespace(id=chunk_id)

    def delete(self, ids: Iterable[str]) -> None:
        self.deleted.extend(sorted(ids))


def test_purge_removes_vectors_absent_from_the_current_chunk_set() -> None:
    """A renamed symbol mints a new chunk id; the old row must go."""
    store = _FakeVectorStore(["keep-a", "keep-b", "stale-renamed"])

    EmbedAndPersistPass()._purge_orphan_vectors(store, {"keep-a", "keep-b"})

    assert store.deleted == ["stale-renamed"]


def test_purge_is_a_noop_when_every_stored_id_is_current() -> None:
    store = _FakeVectorStore(["keep-a", "keep-b"])

    EmbedAndPersistPass()._purge_orphan_vectors(store, {"keep-a", "keep-b"})

    assert store.deleted == []


def test_purge_tolerates_an_empty_store() -> None:
    store = _FakeVectorStore([])

    EmbedAndPersistPass()._purge_orphan_vectors(store, {"keep-a"})

    assert store.deleted == []
