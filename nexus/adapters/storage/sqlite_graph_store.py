"""SQLite-backed :class:`~nexus.core.protocols.GraphStore` implementation.

Why SQLite instead of NetworkX / GraphML / Neo4j:

* It's already installed everywhere (stdlib).
* Incremental updates are single-row writes, not full-graph serialisation.
* Recursive CTEs are fast enough for the traversal patterns Phase 4
  needs, and the cost of a traversal is a join - not a Python loop.
* Each project gets its own ``graph.sqlite`` file, so multi-project is
  naturally handled by opening different files.

The store is a thin layer: it maps each in-memory :class:`~nexus.core.graph.Node`
and :class:`~nexus.core.graph.Edge` to a row, stores the attributes
blob as JSON, and reads them back on load.

Migrations live in ``migrations/`` as plain ``.sql`` files addressed
via :mod:`importlib.resources`. On first open the store creates the
schema if needed; on reopen it does nothing - creation is idempotent.

Transactions wrap the ``persist`` write so a partial failure leaves the
store in its prior state. ``load`` uses a single SELECT per table.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from importlib import resources
from typing import TYPE_CHECKING

from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.outcome import Outcome, Warning

if TYPE_CHECKING:
    from pathlib import Path


class SqliteGraphStore:
    """Store a :class:`Graph` in a per-project SQLite database.

    The store owns its connection for the lifetime of the instance.
    Call :meth:`close` when done; a context-manager form is not yet
    provided because the typical caller is long-lived (the pipeline
    or the MCP server) and holds the store for the duration.
    """

    def __init__(self, path: Path) -> None:
        """Open or create a graph store at ``path``.

        Args:
            path: Filesystem path to the SQLite database file. The
                parent directory must already exist - the
                :class:`~nexus.adapters.storage.project_storage.ProjectStorage`
                repository creates the project directory up front.
        """
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._initialised = False
        # Cached read-side graph, populated on the first :meth:`load`
        # call and cleared by :meth:`persist`, :meth:`clear`, and
        # :meth:`close`. The Phase 4 query engine loads the graph once
        # per tool call today; caching turns a ~600 ms SQLite + JSON
        # decode on the helm-v7 index into ~1 ms after the first hit.
        self._cached_graph: Graph | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self._path,
                isolation_level=None,  # we manage transactions explicitly
                detect_types=sqlite3.PARSE_DECLTYPES,
                check_same_thread=False,  # FastMCP dispatches handlers via thread pool
            )
            self._conn.row_factory = sqlite3.Row
            # WAL gives us concurrent reads during a write, which the
            # Phase 4 query engine will want.
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def initialise(self) -> None:
        """Create or migrate the schema. Idempotent.

        Reads every migration file bundled under
        ``nexus.adapters.storage.migrations`` via
        :mod:`importlib.resources` and applies any whose version is not
        already recorded in ``schema_migrations``.

        Note on transactions: ``sqlite3.Cursor.executescript`` issues an
        implicit ``COMMIT`` before running its payload, which would
        abort any enclosing ``BEGIN``. We instead rely on the
        ``CREATE TABLE IF NOT EXISTS`` guards for idempotency and record
        the applied version in its own insert right after the script
        runs. A once-per-instance ``_initialised`` flag short-circuits
        the work on subsequent calls.
        """
        if self._initialised:
            return

        conn = self._connection()
        cur = conn.cursor()

        migrations = self._load_migrations()
        applied = self._applied_versions(cur, migrations_exists=False)

        for version, sql in migrations:
            if version in applied:
                continue
            cur.executescript(sql)
            cur.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )

        self._initialised = True

    @staticmethod
    def _load_migrations() -> list[tuple[int, str]]:
        migrations_dir = resources.files("nexus.adapters.storage.migrations")
        items: list[tuple[int, str]] = []
        for entry in migrations_dir.iterdir():
            name = entry.name
            if not name.endswith(".sql"):
                continue
            try:
                version = int(name.split("_", 1)[0])
            except ValueError:
                continue
            items.append((version, entry.read_text(encoding="utf-8")))
        items.sort(key=lambda item: item[0])
        return items

    @staticmethod
    def _applied_versions(cur: sqlite3.Cursor, *, migrations_exists: bool) -> set[int]:
        # On a fresh database the migrations table doesn't exist yet.
        # Silently treat "no table" as "no migrations applied".
        try:
            rows = cur.execute("SELECT version FROM schema_migrations").fetchall()
        except sqlite3.OperationalError:
            if migrations_exists:
                raise
            return set()
        return {row["version"] for row in rows}

    def close(self) -> None:
        """Release the underlying SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        self._initialised = False
        self._cached_graph = None

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def persist(self, graph: Graph) -> Outcome[None]:
        """Atomically replace the stored graph with the given one.

        The store is a destination of record: each call clears the
        existing nodes and edges and writes the new set in one
        transaction. Incremental updates - edit this node, add that
        edge - are handled by the pipeline layer which will call
        finer-grained methods once Phase 3 lands.

        Returns:
            An :class:`Outcome` carrying any warnings. Exceptions are
            re-raised; a partial write has been rolled back.
        """
        conn = self._connection()
        self.initialise()
        self._cached_graph = None

        node_ids = {n.id for n in graph.nodes}
        valid_edges = [e for e in graph.edges if e.source in node_ids]
        dangling = [e for e in graph.edges if e.source not in node_ids]

        outcome_warnings: list[Warning] = []
        if dangling:
            outcome_warnings.append(
                Warning(
                    code="dangling_edges_dropped",
                    message=(
                        f"{len(dangling)} edge(s) were dropped because their source node "
                        f"was not present in the graph. This usually means the graph builder "
                        f"emitted an edge before registering the source node."
                    ),
                    context={
                        "count": len(dangling),
                        "sample_sources": [e.source for e in dangling[:5]],
                    },
                )
            )

        cur = conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute("DELETE FROM edges")
            cur.execute("DELETE FROM nodes")
            cur.execute("DELETE FROM warnings")

            cur.executemany(
                "INSERT INTO nodes (id, kind, name, attributes) VALUES (?, ?, ?, ?)",
                [(n.id, n.kind.value, n.name, json.dumps(n.attributes)) for n in graph.nodes],
            )

            cur.executemany(
                "INSERT INTO edges (source, target, kind, attributes) VALUES (?, ?, ?, ?)",
                [(e.source, e.target, e.kind.value, json.dumps(e.attributes)) for e in valid_edges],
            )

            all_warnings = list(graph.warnings) + outcome_warnings
            cur.executemany(
                "INSERT INTO warnings (code, message, context) VALUES (?, ?, ?)",
                [(w.code, w.message, json.dumps(w.context)) for w in all_warnings],
            )

            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return Outcome.success(None, warnings=outcome_warnings if outcome_warnings else None)

    def load(self) -> Graph:
        """Reload the persisted graph into a typed in-memory shape.

        The first call materialises the graph from SQLite and caches
        it. Subsequent calls return the cached instance so tools in a
        long-lived query session don't pay the SQLite + JSON decode
        cost on every invocation. The cache is invalidated by any
        mutating method (:meth:`persist`, :meth:`clear`).
        """
        if self._cached_graph is not None:
            return self._cached_graph

        conn = self._connection()
        self.initialise()

        graph = Graph()

        for row in conn.execute("SELECT id, kind, name, attributes FROM nodes ORDER BY id"):
            graph.add_node(
                Node(
                    id=row["id"],
                    kind=NodeKind(row["kind"]),
                    name=row["name"],
                    attributes=json.loads(row["attributes"]),
                ),
            )

        for row in conn.execute(
            "SELECT source, target, kind, attributes FROM edges ORDER BY id",
        ):
            graph.add_edge(
                Edge(
                    source=row["source"],
                    target=row["target"],
                    kind=EdgeKind(row["kind"]),
                    attributes=json.loads(row["attributes"]),
                ),
            )

        for row in conn.execute("SELECT code, message, context FROM warnings ORDER BY id"):
            graph.add_warning(
                Warning(
                    code=row["code"],
                    message=row["message"],
                    context=json.loads(row["context"]),
                ),
            )

        self._cached_graph = graph
        return graph

    def clear(self) -> None:
        """Drop every row (but keep the schema)."""
        conn = self._connection()
        self.initialise()
        with conn:
            conn.execute("DELETE FROM edges")
            conn.execute("DELETE FROM nodes")
            conn.execute("DELETE FROM warnings")
        self._cached_graph = None

    # ------------------------------------------------------------------
    # Introspection for tests
    # ------------------------------------------------------------------

    def node_count(self) -> int:
        """Return the number of persisted nodes."""
        conn = self._connection()
        self.initialise()
        row = conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()
        return int(row["n"])

    def edge_count(self) -> int:
        """Return the number of persisted edges."""
        conn = self._connection()
        self.initialise()
        row = conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()
        return int(row["n"])
