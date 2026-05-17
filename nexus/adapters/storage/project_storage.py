"""Per-project storage repository.

``ProjectStorage`` owns the ``~/.nexus/projects/<slug>/`` directory
layout described in ``internal_docs/07-storage-layer.md``:

.. code-block:: text

    ~/.nexus/projects/<slug>/
    ├── graph.sqlite          ← SqliteGraphStore
    ├── vectors/              ← LanceDbVectorStore dataset
    ├── reflection.json       ← Phase 1 output (written by the pipeline)
    └── meta.json             ← detected profile, last-indexed commit, stats

The repository is the only class that knows about the directory layout;
every other consumer receives protocol-typed handles for the graph
store, vector store, and the reflection path. This is the adapter-layer
equivalent of the Repository pattern: hide the filesystem details
behind a narrow API so callers never string-concatenate paths.

``ProjectStorage`` is intentionally short on policy. It creates the
directory on ``initialise``, opens the stores lazily, and closes them
cleanly. Meta.json I/O is typed via a small Pydantic model so schema
drift is loud.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nexus.adapters.storage.lancedb_vector_store import LanceDbVectorStore
from nexus.adapters.storage.sqlite_graph_store import SqliteGraphStore
from nexus.core.reflection.document import (  # noqa: TC001 — runtime Pydantic field type
    PackageMetadata,
)

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_META_SCHEMA_MAJOR = 1


class ProjectMeta(BaseModel):
    """Persisted metadata for a project's storage directory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = f"{PROJECT_META_SCHEMA_MAJOR}.1"
    project_slug: str
    project_path: str
    detected_profile: str | None = None
    profile_source: str = "auto"
    profile_match_score: float | None = None
    all_match_scores: dict[str, float] = Field(default_factory=dict)
    laravel_version: str | None = None
    last_indexed_commit: str | None = None
    indexed_at: str | None = None
    updated_at: str | None = Field(
        default=None,
        description="ISO-8601 UTC timestamp the writer stamped on the meta file.",
    )
    node_count: int | None = None
    edge_count: int | None = None
    embedder_id: str | None = None
    lsp_server: str | None = Field(
        default=None,
        description=(
            "Descriptor of the LSP server (binary path or name) used to "
            "populate CALLS edges, or ``None`` when LSP enrichment was "
            "skipped. ``nexus index status`` surfaces this so an agent "
            "can tell whether call-graph queries are grounded."
        ),
    )
    kind: Literal["project", "package"] = "project"
    package: PackageMetadata | None = None
    build_mode: Literal["in-repo", "nexus-driven"] | None = None
    source_path: str | None = None


class ProjectStorageError(Exception):
    """Raised for filesystem-level problems with the project directory."""


@dataclass(slots=True)
class ProjectStorage:
    """Owns the per-project directory layout under ``~/.nexus/projects/<slug>/``.

    The repository is a thin façade: its methods return protocol-typed
    handles for the graph and vector stores, the path to the reflection
    document, and typed access to the meta.json file.

    Instances are opened once per indexing run and closed when the run
    ends. Neither the constructor nor ``initialise`` perform any writes
    beyond directory creation — the stores are created lazily when the
    caller asks for them, so an unused :class:`ProjectStorage` leaves no
    trace on disk.
    """

    root: Path
    slug: str
    _graph: SqliteGraphStore | None = field(default=None, init=False, repr=False)
    _vectors: LanceDbVectorStore | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Directory layout
    # ------------------------------------------------------------------

    @property
    def project_dir(self) -> Path:
        """Absolute path of the per-project directory."""
        return self.root / "projects" / self.slug

    @property
    def graph_path(self) -> Path:
        """Path to the SQLite graph store file."""
        return self.project_dir / "graph.sqlite"

    @property
    def vectors_path(self) -> Path:
        """Path to the LanceDB dataset directory."""
        return self.project_dir / "vectors"

    @property
    def reflection_path(self) -> Path:
        """Path where the Phase 1 PHP extractor writes ``reflection.json``."""
        return self.project_dir / "reflection.json"

    @property
    def meta_path(self) -> Path:
        """Path to the project metadata JSON file."""
        return self.project_dir / "meta.json"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialise(self) -> None:
        """Create the per-project directory tree if it doesn't already exist.

        Safe to call multiple times. Does NOT create the SQLite or
        LanceDB stores — those are created on first use by the accessor
        methods below.
        """
        self.project_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        """Release any opened store handles.

        Callers that hold a :class:`ProjectStorage` for the life of a
        pipeline run should call this before the process exits. Tests
        use it to detect file-descriptor leaks.
        """
        if self._graph is not None:
            self._graph.close()
            self._graph = None
        if self._vectors is not None:
            self._vectors.close()
            self._vectors = None

    # ------------------------------------------------------------------
    # Store accessors
    # ------------------------------------------------------------------

    def graph(self) -> SqliteGraphStore:
        """Return the (lazily-opened) SQLite graph store."""
        if self._graph is None:
            self.initialise()
            self._graph = SqliteGraphStore(self.graph_path)
            self._graph.initialise()
        return self._graph

    def vectors(self, *, dimensions: int) -> LanceDbVectorStore:
        """Return the (lazily-opened) LanceDB vector store.

        The dimensionality must be supplied because LanceDB's schema
        fixes the vector length at table creation. Callers that persist
        their chosen embedder in :class:`ProjectMeta` can read it back
        before calling this.
        """
        if self._vectors is None:
            self.initialise()
            self._vectors = LanceDbVectorStore(self.vectors_path, dimensions=dimensions)
        return self._vectors

    # ------------------------------------------------------------------
    # Meta.json I/O
    # ------------------------------------------------------------------

    def read_meta(self) -> ProjectMeta | None:
        """Read the persisted ``meta.json`` if present.

        Returns:
            A :class:`ProjectMeta` instance, or ``None`` if the file
            doesn't exist yet (first-time indexing).

        Raises:
            ProjectStorageError: the file exists but can't be parsed.
        """
        if not self.meta_path.is_file():
            return None
        try:
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise ProjectStorageError(f"Cannot read {self.meta_path}: {e}") from e

        try:
            return ProjectMeta.model_validate(raw)
        except ValidationError as e:
            raise ProjectStorageError(f"Invalid meta.json at {self.meta_path}: {e}") from e

    def write_meta(self, meta: ProjectMeta) -> None:
        """Atomically write ``meta.json`` via a sibling temp file + rename.

        The atomic rename means a crashing writer never leaves behind a
        half-written meta file. ``updated_at`` is stamped on write even
        if the caller left it unset.
        """
        self.initialise()

        payload: dict[str, Any] = meta.model_dump()
        payload["updated_at"] = datetime.now(UTC).isoformat()

        tmp = self.meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.meta_path)
