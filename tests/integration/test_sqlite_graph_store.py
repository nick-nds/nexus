"""Integration tests for the SQLite graph store.

These tests touch a real SQLite file under ``tmp_path``. They are fast
(a few milliseconds each) but marked as ``integration`` so they can be
selectively skipped in minimal CI runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from nexus.adapters.storage import SqliteGraphStore
from nexus.core.graph.builder import GraphBuilder
from nexus.core.graph.graph import Graph
from nexus.core.graph.types import Edge, EdgeKind, Node, NodeKind
from nexus.core.outcome import Warning
from nexus.core.reflection import load_reflection

pytestmark = pytest.mark.integration


FIXTURE = Path(__file__).parent.parent / "fixtures" / "reflection-samples" / "momskitchen.json"


@dataclass(frozen=True)
class StubProfile:
    name: str = "test-profile"
    custom_bases: dict[str, str] = None  # type: ignore[assignment]
    custom_suffixes: dict[str, str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.custom_bases is None:
            object.__setattr__(self, "custom_bases", {})
        if self.custom_suffixes is None:
            object.__setattr__(self, "custom_suffixes", {})


@pytest.fixture
def store(tmp_path: Path):
    path = tmp_path / "graph.sqlite"
    store = SqliteGraphStore(path)
    store.initialise()
    yield store
    store.close()


def make_simple_graph() -> Graph:
    g = Graph()
    g.add_node(Node(id="a", kind=NodeKind.MODEL, name="A", attributes={"x": 1}))
    g.add_node(Node(id="b", kind=NodeKind.CONTROLLER, name="B"))
    g.add_edge(Edge(source="a", target="b", kind=EdgeKind.CALLS, attributes={"file": "x.php"}))
    g.add_warning(Warning(code="w1", message="msg", context={"k": "v"}))
    return g


class TestLifecycle:
    def test_initialise_is_idempotent(self, tmp_path: Path) -> None:
        path = tmp_path / "graph.sqlite"
        store = SqliteGraphStore(path)
        store.initialise()
        # Second call must not raise.
        store.initialise()
        store.close()

    def test_close_releases_connection(self, tmp_path: Path) -> None:
        path = tmp_path / "graph.sqlite"
        store = SqliteGraphStore(path)
        store.initialise()
        store.close()
        # Reopening should work cleanly.
        store2 = SqliteGraphStore(path)
        store2.initialise()
        store2.close()


class TestPersistAndLoad:
    def test_round_trip_simple_graph(self, store: SqliteGraphStore) -> None:
        original = make_simple_graph()

        result = store.persist(original)
        assert result.ok

        loaded = store.load()
        assert len(loaded.nodes) == 2
        assert len(loaded.edges) == 1
        assert len(loaded.warnings) == 1

        # Attributes round-trip via JSON.
        a = loaded.node_by_id("a")
        assert a is not None
        assert a.attributes == {"x": 1}

    def test_persist_replaces_existing(self, store: SqliteGraphStore) -> None:
        store.persist(make_simple_graph())
        assert store.node_count() == 2

        # Persist a smaller graph - the store is destination-of-record.
        smaller = Graph()
        smaller.add_node(Node(id="c", kind=NodeKind.MODEL, name="C"))
        store.persist(smaller)

        assert store.node_count() == 1
        loaded = store.load()
        assert loaded.node_by_id("a") is None
        assert loaded.node_by_id("c") is not None

    def test_counts_match_after_persist(self, store: SqliteGraphStore) -> None:
        g = make_simple_graph()
        store.persist(g)

        assert store.node_count() == 2
        assert store.edge_count() == 1

    def test_clear_empties_store(self, store: SqliteGraphStore) -> None:
        store.persist(make_simple_graph())
        store.clear()

        assert store.node_count() == 0
        assert store.edge_count() == 0


class TestAgainstRealFixture:
    """Persist the real momskitchen-derived graph and verify the counts."""

    def test_round_trip_momskitchen_graph(self, store: SqliteGraphStore) -> None:
        document = load_reflection(FIXTURE)
        built = GraphBuilder().build(document, StubProfile()).value

        store.persist(built)
        reloaded = store.load()

        assert len(reloaded.nodes) == len(built.nodes)
        assert len(reloaded.edges) == len(built.edges)
        # Warnings are persisted too.
        assert len(reloaded.warnings) == len(built.warnings)

    def test_reload_preserves_node_kinds(self, store: SqliteGraphStore) -> None:
        document = load_reflection(FIXTURE)
        built = GraphBuilder().build(document, StubProfile()).value
        store.persist(built)

        reloaded = store.load()

        original_kinds = sorted(n.kind.value for n in built.nodes)
        reloaded_kinds = sorted(n.kind.value for n in reloaded.nodes)
        assert original_kinds == reloaded_kinds


class TestMigrationsTable:
    def test_migration_version_recorded(self, tmp_path: Path) -> None:
        import sqlite3

        path = tmp_path / "graph.sqlite"
        store = SqliteGraphStore(path)
        store.initialise()
        store.close()

        # Connect raw and inspect the migrations table.
        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version",
            ).fetchall()
            # 0001_initial + 0002_rename_controller_method (audit P0-3).
            # Bump this expected count whenever a new migration ships.
            assert len(rows) == 2
            assert [r[0] for r in rows] == [1, 2]
        finally:
            conn.close()

    def test_indices_exist(self, tmp_path: Path) -> None:
        import sqlite3

        path = tmp_path / "graph.sqlite"
        store = SqliteGraphStore(path)
        store.initialise()
        store.close()

        conn = sqlite3.connect(path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'",
            ).fetchall()
            names = {row[0] for row in rows}
            assert "idx_nodes_kind" in names
            assert "idx_edges_source" in names
            assert "idx_edges_target" in names
        finally:
            conn.close()
