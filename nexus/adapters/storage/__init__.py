"""Storage adapters: SQLite graph store, LanceDB vector store, project storage.

See ``internal_docs/07-storage-layer.md`` for the per-project directory
layout and schema design rationale.
"""

from nexus.adapters.storage.lancedb_vector_store import (
    LanceDbVectorStore,
    LanceSearchHit,
    LanceVectorRecord,
)
from nexus.adapters.storage.project_storage import (
    PROJECT_META_SCHEMA_MAJOR,
    ProjectMeta,
    ProjectStorage,
    ProjectStorageError,
)
from nexus.adapters.storage.sqlite_graph_store import SqliteGraphStore

__all__ = [
    "PROJECT_META_SCHEMA_MAJOR",
    "LanceDbVectorStore",
    "LanceSearchHit",
    "LanceVectorRecord",
    "ProjectMeta",
    "ProjectStorage",
    "ProjectStorageError",
    "SqliteGraphStore",
]
