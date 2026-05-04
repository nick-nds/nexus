-- Nexus graph store — initial schema.
--
-- Two primary tables (nodes, edges) plus a single-row migrations table
-- so the store can detect which migrations have been applied. Covering
-- indices support the query engine's common lookups (Phase 4) without
-- requiring extra joins.
--
-- The schema is intentionally narrow — every in-memory Node and Edge
-- field maps 1:1 to a column, with the free-form ``attributes`` blob
-- serialised as JSON TEXT. This avoids Pydantic-on-read overhead and
-- matches the way SQLite's JSON functions can query nested values when
-- the query engine needs them.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    attributes  TEXT NOT NULL        -- JSON blob
);

CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);

CREATE TABLE IF NOT EXISTS edges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    target      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    attributes  TEXT NOT NULL,       -- JSON blob
    FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source, kind);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target, kind);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);

CREATE TABLE IF NOT EXISTS warnings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    message     TEXT NOT NULL,
    context     TEXT NOT NULL
);
