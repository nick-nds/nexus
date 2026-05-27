"""Protocol definitions for every Nexus extension point.

Every cross-boundary type in Nexus is a :class:`typing.Protocol`. Concrete
implementations live in :mod:`nexus.adapters` (OSS) or in plugin packages
discovered through :mod:`nexus.plugins`. The pure core code in
:mod:`nexus.core` only ever depends on these abstract shapes.

Why protocols and not abstract base classes:

* Structural typing means a third-party plugin can ship a class that
  satisfies one of these shapes without inheriting from anything Nexus
  exports. The pro tier and any future community plugin can drop in a
  new embedder, vector store, or graph store backend without importing
  from ``nexus.adapters``.
* Protocols compose more cleanly with generics than ABCs do.
* Tests use plain in-memory implementations (handwritten classes that
  match the shape) rather than Mock objects.

Where an :class:`typing.Protocol` is insufficient - typically because we
need ``isinstance`` checks at runtime - we use :func:`typing.runtime_checkable`
sparingly. None of the protocols in this module require it today.

Each protocol carries a contract test in ``tests/contract/`` that every
implementation must pass. Adding a new backend means writing one class
and pointing the contract test parametrisation at it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence
    from pathlib import Path

    from nexus.core.graph.graph import Graph
    from nexus.core.lsp import FileLocation
    from nexus.core.outcome import Outcome
    from nexus.core.reflection.document import ReflectionDocument


# ----------------------------------------------------------------------------
# Embedder
# ----------------------------------------------------------------------------


class Embedder(Protocol):
    """Pluggable text-embedding backend.

    Implementations live in :mod:`nexus.adapters.embedders` (Phase 3) and
    in pro-tier plugin packages. The contract is intentionally tiny:
    given a list of strings, return one vector per string. Backends are
    free to batch internally; callers should not assume any particular
    batch size.

    Implementations MUST be deterministic for a given ``model_id`` -
    embedding the same text twice with the same model returns the same
    vector. The embedding cache (Phase 3) keys on the SHA-256 of the
    enriched text plus the model id, so non-determinism would cause
    silent cache misses.
    """

    @property
    def model_id(self) -> str:
        """Stable identifier for the embedding model.

        Used as part of the cache key. Changing models invalidates the
        cache for that backend, which is the intended behaviour. The id
        should encode both the provider and the version, e.g.
        ``voyage:voyage-code-3`` or ``ollama:nomic-embed-text``.
        """
        ...

    @property
    def dimensions(self) -> int:
        """Vector dimensionality the backend produces."""
        ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of strings into vectors.

        Args:
            texts: Non-empty sequence of strings to embed.

        Returns:
            One vector per input string, in the same order. Each vector
            has length :attr:`dimensions`.
        """
        ...

    def estimate_tokens(self, text: str) -> int:
        """Approximate the token count for one text.

        Used by the cost estimator (Phase 3) to compute a dollar
        estimate before any paid embedding call is made. Local backends
        may return a rough character-based approximation; paid backends
        should use the official tokenizer.
        """
        ...


# ----------------------------------------------------------------------------
# Vector store
# ----------------------------------------------------------------------------


class VectorStore(Protocol):
    """Pluggable vector index for chunk embeddings.

    A :class:`VectorStore` holds vectors plus a small per-vector payload
    (the chunk id, the source file, the node id the chunk is associated
    with, the model that produced the embedding) and answers similarity
    queries. The contract is small enough that LanceDB, Chroma,
    in-memory test doubles, and any future backend can satisfy it.
    """

    def upsert(self, items: Iterable[VectorRecord]) -> None:
        """Insert or update a batch of vector records by ``id``."""
        ...

    def delete(self, ids: Iterable[str]) -> None:
        """Delete records by id. Missing ids are silently ignored."""
        ...

    def search(
        self,
        query: Sequence[float],
        *,
        top_k: int,
    ) -> list[VectorSearchHit]:
        """Return the ``top_k`` nearest neighbours of ``query``.

        Implementations should use cosine similarity by default; the
        contract test in ``tests/contract/`` parametrises over backends
        and asserts the same ranking on a fixture vector set.
        """
        ...

    def count(self) -> int:
        """Number of records currently in the store."""
        ...

    def iter_records(self) -> Iterator[VectorRecord]:
        """Yield every stored record in unspecified order.

        Used by query-time tools that need to look up a chunk by an
        attribute in :attr:`VectorRecord.payload` (e.g.
        ``get_node_body`` walks every chunk to find the one whose
        ``node_id`` matches). The contract does not promise any
        particular order; callers must build their own lookup table.

        Implementations may stream from a cursor or load eagerly.
        Implementations whose store is empty should yield nothing
        (not raise).
        """
        ...

    def close(self) -> None:
        """Release any underlying resources (file handles, sockets)."""
        ...


class VectorRecord(Protocol):
    """One row in a :class:`VectorStore`.

    The ``payload`` field is intentionally loose - different backends
    may flatten it into typed columns or store it as JSON. Implementers
    should treat keys with reserved meaning (``chunk_id``, ``node_id``,
    ``model_id``) consistently.
    """

    id: str
    vector: Sequence[float]
    payload: dict[str, object]


class VectorSearchHit(Protocol):
    """One result row from :meth:`VectorStore.search`."""

    id: str
    score: float
    payload: dict[str, object]


# ----------------------------------------------------------------------------
# Graph store
# ----------------------------------------------------------------------------


class GraphStore(Protocol):
    """Pluggable persistent graph backend.

    The OSS implementation in :mod:`nexus.adapters.storage.sqlite_graph_store`
    uses SQLite with adjacency tables; future backends could include
    DuckDB, Neo4j Embedded, or an in-memory test store. All backends
    must satisfy the same contract test in ``tests/contract/``.
    """

    def initialise(self) -> None:
        """Create or migrate the schema. Idempotent."""
        ...

    def persist(self, graph: Graph) -> Outcome[None]:
        """Persist a built graph atomically.

        Implementations should wrap the write in a single transaction
        so that a failure mid-write does not leave the store in a
        partially-updated state.
        """
        ...

    def load(self) -> Graph:
        """Reload the persisted graph back into a typed in-memory shape."""
        ...

    def clear(self) -> None:
        """Drop all rows. Used by ``nexus index clear`` (Phase 5)."""
        ...

    def close(self) -> None:
        """Release any underlying resources."""
        ...


# ----------------------------------------------------------------------------
# Reflection loader and PHP extractor
# ----------------------------------------------------------------------------


class ReflectionLoader(Protocol):
    """Reads a reflection.json document from disk into a typed model.

    The OSS implementation in :mod:`nexus.core.reflection.loader` is the
    only one we ship; the protocol exists so tests can supply
    pre-built documents without round-tripping through JSON.
    """

    def load(self, path: Path) -> ReflectionDocument:
        """Parse and validate a reflection document."""
        ...


class Extractor(Protocol):
    """Drives the PHP-side ``nexus:extract`` command.

    The OSS implementation in :mod:`nexus.adapters.extractor` (Phase 3)
    is a subprocess wrapper around ``php artisan nexus:extract``. The
    protocol decouples the pipeline from the specific subprocess
    mechanics so tests can substitute a fixture-based extractor.
    """

    def extract(self, project_path: Path, *, output_path: Path) -> Outcome[Path]:
        """Run the extractor against ``project_path`` and write the JSON.

        Returns the path to the written reflection.json on success, or
        an :class:`Outcome` carrying errors describing the failure.
        """
        ...


# ----------------------------------------------------------------------------
# Language server (LSP)
# ----------------------------------------------------------------------------


class Lsp(Protocol):
    """A language-server boundary used to enrich the graph with CALLS edges.

    The pipeline asks an LSP for the references of every method node so
    it can connect callers to callees. Implementations live in
    :mod:`nexus.adapters.lsp`; the OSS shipped backends are
    :class:`~nexus.adapters.lsp.NullLsp` (no-op fallback when no server
    is available) and ``IntelephenseLsp`` (subprocess wrapper around
    ``intelephense --stdio``, planned in subtask 1.2).

    Line and column positions exchanged through this protocol are
    **1-indexed** - adapters wrapping LSP servers convert from the
    spec's 0-indexed positions at the boundary. See :mod:`nexus.core.lsp`.
    """

    def prepare(self, workspace_root: Path) -> None:
        """Initialise the language server for ``workspace_root``.

        Must be idempotent - calling it twice for the same root is a
        no-op. Concrete adapters use this to send the LSP ``initialize``
        and ``initialized`` notifications and to open the project's
        files.
        """
        ...

    def references(
        self,
        file: Path,
        line: int,
        character: int,
    ) -> list[FileLocation]:
        """Return every reference to the symbol at ``(file, line, character)``.

        Line and character are 1-indexed. The returned list excludes
        the declaration itself: only call-sites and other usages.
        Adapters that cannot answer the query (server unresponsive,
        symbol not found) return an empty list rather than raising.
        """
        ...

    def close(self) -> None:
        """Release any resources held by the language server.

        Concrete adapters terminate the LSP subprocess here. Calling
        :meth:`close` on an already-closed server is a no-op.
        """
        ...


# ----------------------------------------------------------------------------
# Retriever (semantic search facade)
# ----------------------------------------------------------------------------


class Retriever(Protocol):
    """Returns chunks relevant to a free-text query.

    Used by the semantic search tool in Phase 4. Wraps both the
    :class:`Embedder` (to embed the query) and the :class:`VectorStore`
    (to look up nearest neighbours), plus optional graph-aware
    re-ranking.
    """

    def retrieve(self, query: str, *, top_k: int) -> list[VectorSearchHit]:
        """Return the top ``top_k`` chunks ranked by relevance to ``query``."""
        ...


# ----------------------------------------------------------------------------
# Profile
# ----------------------------------------------------------------------------


class Profile(Protocol):
    """A project-conventions profile loaded from a built-in or user YAML.

    The full implementation lives in :mod:`nexus.profiles.model`; this
    protocol exists so the graph builder and other domain code can
    accept "any profile" without importing the concrete model.

    The fields here are the minimum a downstream consumer needs:
    name, custom-base mapping, custom-suffix mapping, and module pattern.
    """

    @property
    def name(self) -> str:
        """Stable profile identifier (e.g. ``laravel-ddd-cqrs``)."""
        ...

    @property
    def custom_bases(self) -> dict[str, str]:
        """Map of fully-qualified base class → emitted kind label."""
        ...

    @property
    def custom_suffixes(self) -> dict[str, str]:
        """Map of class-name suffix → emitted kind label."""
        ...


# ----------------------------------------------------------------------------
# Project storage repository (query-time view)
# ----------------------------------------------------------------------------


class ProjectStorageProtocol(Protocol):
    """Read-side view of a per-project storage directory.

    The concrete implementation lives in
    :mod:`nexus.adapters.storage.project_storage`; this protocol
    exists so Phase 4's query engine can accept "any project
    storage" without the core layer importing an adapter.

    Only the handles the query engine needs are exposed here:
    the graph store and the lazy vector-store accessor. The
    meta.json reader is exposed too because tools that surface
    "when was this indexed" want the stamped metadata.
    """

    @property
    def slug(self) -> str:
        """Project slug used to namespace the storage directory."""
        ...

    def graph(self) -> GraphStore:
        """Return the opened graph store for this project."""
        ...

    def vectors(self, *, dimensions: int) -> VectorStore:
        """Return the opened vector store for this project.

        Lazy: the LanceDB dataset is only created on first access.
        """
        ...
