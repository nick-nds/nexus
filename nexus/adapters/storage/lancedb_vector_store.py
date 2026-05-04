"""LanceDB-backed vector store.

Phase 2 ships the adapter shell so the contract is real and can be
swapped out; Phase 3 will populate it with actual chunk embeddings
once the indexing pipeline is alive. The schema is intentionally
minimal: id, vector, payload (JSON). When we need typed columns for
speed on Phase 4's query paths we'll add them as an additive
migration.

Why LanceDB over ChromaDB:

* Columnar on-disk format — faster cold reads than Chroma's SQLite
  backend once the store gets large (the helm-v7 scale).
* No background daemon; the library opens and reads files directly.
* Cleaner per-project isolation: each project's store is a directory
  under ``~/.nexus/projects/<slug>/vectors/``.

The :class:`~nexus.core.protocols.VectorStore` contract is satisfied
structurally — this class doesn't inherit from the protocol, it just
matches the shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import lancedb
import pyarrow as pa

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class LanceVectorRecord:
    """A single vector row for upsert into a :class:`LanceDbVectorStore`.

    Structurally satisfies :class:`~nexus.core.protocols.VectorRecord`.
    """

    id: str
    vector: Sequence[float]
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LanceSearchHit:
    """A single row returned by :meth:`LanceDbVectorStore.search`.

    Structurally satisfies :class:`~nexus.core.protocols.VectorSearchHit`.
    """

    id: str
    score: float
    payload: dict[str, Any]


class LanceDbVectorStore:
    """Per-project vector store backed by LanceDB.

    One instance represents one LanceDB dataset directory. Instances
    lazily create the dataset on the first upsert so an unused store
    doesn't litter disk.

    The store is created with a fixed vector dimensionality supplied at
    construction. Switching embedders (which changes the dimensionality)
    requires :meth:`clear` followed by a re-insert with the new vectors.
    The embedding-cache layer in Phase 3 keys on ``model_id`` so this
    is safe: an incompatible vector simply doesn't show up.
    """

    _TABLE_NAME = "chunks"

    def __init__(self, path: Path, *, dimensions: int) -> None:
        """Open or create a LanceDB store at ``path``.

        Args:
            path: Directory path for the LanceDB dataset. LanceDB will
                create subdirectories inside.
            dimensions: Vector length. Must be constant across all
                records in the store.
        """
        self._path = path
        self._dimensions = dimensions
        self._db: lancedb.DBConnection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _connection(self) -> lancedb.DBConnection:
        if self._db is None:
            # LanceDB creates the directory on connect if it doesn't
            # already exist — no explicit initialisation step needed.
            self._path.mkdir(parents=True, exist_ok=True)
            self._db = lancedb.connect(self._path)
        return self._db

    def _schema(self) -> pa.Schema:
        return pa.schema(
            [
                pa.field("id", pa.string(), nullable=False),
                pa.field("vector", pa.list_(pa.float32(), list_size=self._dimensions)),
                pa.field("payload", pa.string()),
            ],
        )

    def _ensure_table(self) -> lancedb.table.Table:
        db = self._connection()
        if self._TABLE_NAME in self._existing_tables(db):
            return db.open_table(self._TABLE_NAME)
        return db.create_table(self._TABLE_NAME, schema=self._schema())

    def close(self) -> None:
        """Release any held file handles.

        LanceDB's Python binding uses Arrow-backed file mappings. There
        is no explicit close operation at the DB level in 0.29; dropping
        the reference releases the mapping. This method exists so the
        :class:`VectorStore` protocol is satisfied and so tests can
        detect leaks via proc-fs counting.
        """
        self._db = None

    @staticmethod
    def _existing_tables(db: lancedb.DBConnection) -> list[str]:
        """Return the list of table names in ``db``.

        Wraps the slightly awkward LanceDB 0.29 API: ``list_tables()``
        returns a ``ListTablesResponse`` object with a ``.tables``
        attribute rather than a plain list. Centralising the accessor
        means the rest of the adapter can treat it as a normal list.
        """
        response = db.list_tables()
        tables = getattr(response, "tables", None)
        if isinstance(tables, list):
            return [str(t) for t in tables]
        # Fall back to iterating — earlier LanceDB versions returned a
        # plain list from list_tables(). Support both transparently.
        try:
            return [str(t) for t in response]
        except TypeError:
            return []

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def upsert(self, items: Iterable[LanceVectorRecord]) -> None:
        """Insert or update records by id.

        LanceDB doesn't expose a true atomic upsert in the stable API
        we're targeting; we emulate it by deleting then re-inserting
        each id. Bulk call sites (the indexing pipeline) should batch.
        """
        records = list(items)
        if not records:
            return

        table = self._ensure_table()

        ids = [r.id for r in records]
        # Escape ids for the DELETE WHERE clause. Ids should not contain
        # single quotes but we're defensive.
        escaped = [i.replace("'", "''") for i in ids]
        in_clause = ", ".join(f"'{i}'" for i in escaped)
        table.delete(f"id IN ({in_clause})")

        rows = [
            {
                "id": r.id,
                "vector": list(r.vector),
                "payload": json.dumps(r.payload),
            }
            for r in records
        ]
        table.add(rows)

    def delete(self, ids: Iterable[str]) -> None:
        """Delete records by id; missing ids are silently skipped."""
        id_list = list(ids)
        if not id_list:
            return
        db = self._connection()
        if self._TABLE_NAME not in self._existing_tables(db):
            return
        table = db.open_table(self._TABLE_NAME)
        escaped = [i.replace("'", "''") for i in id_list]
        in_clause = ", ".join(f"'{i}'" for i in escaped)
        table.delete(f"id IN ({in_clause})")

    def search(self, query: Sequence[float], *, top_k: int) -> list[LanceSearchHit]:
        """Return the ``top_k`` nearest neighbours by cosine distance."""
        db = self._connection()
        if self._TABLE_NAME not in self._existing_tables(db):
            return []

        table = db.open_table(self._TABLE_NAME)
        results = table.search(list(query)).metric("cosine").limit(top_k).to_list()

        hits: list[LanceSearchHit] = []
        for row in results:
            payload_raw = row.get("payload", "{}")
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
            # LanceDB returns a ``_distance`` column; convert to a
            # similarity score (1 - distance is the convention for
            # cosine in 0..1 range).
            distance = float(row.get("_distance", 0.0))
            score = 1.0 - distance
            hits.append(
                LanceSearchHit(
                    id=str(row["id"]),
                    score=score,
                    payload=payload,
                ),
            )
        return hits

    def count(self) -> int:
        """Return the number of rows in the store."""
        db = self._connection()
        if self._TABLE_NAME not in self._existing_tables(db):
            return 0
        table = db.open_table(self._TABLE_NAME)
        return int(table.count_rows())
